from typing import Any, Dict, List, Optional, Tuple, Callable, Iterator
import logging
import numpy as np
from pathlib import Path
import tempfile
import shutil

from shinka.llm import LLMClient
from shinka.prompts.prompts_novelty import NOVELTY_SYSTEM_MSG, NOVELTY_USER_MSG
from shinka.database import Program

# Type for agent runner function
AgentRunner = Callable[..., Iterator[Dict[str, Any]]]

logger = logging.getLogger(__name__)


class NoveltyJudge:
    def __init__(
        self,
        novelty_llm_client: Optional[LLMClient],
        language: str,
        similarity_threshold: float = 0.85,
        max_novelty_attempts: int = 3,
        agentic_mode: bool = False,
        agent_runner: Optional[AgentRunner] = None,
        agent_config: Optional[Any] = None,
        code_loader: Optional[Callable[[Program], str]] = None,
        error_accepts: bool = False,
        exclude_parent: bool = False,
    ):
        self.llm = novelty_llm_client
        self.language = language
        self.similarity_threshold = similarity_threshold
        self.max_novelty_attempts = max_novelty_attempts
        self.agentic_mode = agentic_mode
        self.agent_runner = agent_runner
        self.agent_config = agent_config
        self.code_loader = code_loader
        self.error_accepts = error_accepts
        self.exclude_parent = exclude_parent

    def should_check_novelty(
        self,
        code_embedding: List[float],
        generation: int,
        parent_program: Optional[Program],
        database,
    ) -> bool:
        """
        Check if novelty assessment should be performed.

        Args:
            code_embedding: Embedding vector of the proposed code
            generation: Current generation number
            parent_program: Parent program
            database: Database instance for similarity computation

        Returns:
            Boolean indicating if novelty check should be performed
        """
        if not code_embedding or generation == 0 or not parent_program:
            return False

        # Check if parent program has island information and islands are initialized
        if (
            parent_program.island_idx is not None
            and hasattr(database, "island_manager")
            and database.island_manager is not None
            and hasattr(database.island_manager, "are_all_islands_initialized")
            and database.island_manager.are_all_islands_initialized()
        ):
            return True

        return False

    def assess_novelty_with_rejection_sampling(
        self,
        proposed_text: str,
        code_embedding: List[float],
        parent_program: Program,
        database,
    ) -> Tuple[bool, dict]:
        """
        Perform novelty assessment with rejection sampling.

        Args:
            proposed_text: Text representation of the candidate artifacts
            code_embedding: Embedding vector of the proposed artifacts
            parent_program: Parent program for island-based similarity
            database: Database instance for similarity computation

        Returns:
            Tuple of (should_accept, novelty_metadata)
        """
        novelty_metadata = {
            "novelty_checks_performed": 0,
            "novelty_total_cost": 0.0,
            "novelty_explanation": "",
            "max_similarity": 0.0,
            "similarity_scores": [],
        }

        for attempt in range(self.max_novelty_attempts):
            # Compute similarities with programs in island
            # If exclude_parent is True, exclude the parent program from comparison
            exclude_id = parent_program.id if self.exclude_parent else None
            similarity_scores = database.compute_similarity(
                code_embedding, parent_program.island_idx,
                exclude_program_id=exclude_id
            )

            if not similarity_scores:
                logger.info(
                    f"NOVELTY CHECK {attempt + 1}/{self.max_novelty_attempts}: "
                    "Accepting program due to no similarity scores."
                )
                novelty_metadata["similarity_scores"] = []
                return True, novelty_metadata

            max_similarity = max(similarity_scores)
            sorted_similarity_scores = sorted(similarity_scores, reverse=True)
            formatted_similarities = [f"{s:.2f}" for s in sorted_similarity_scores]

            logger.info(f"Top-5 similarity scores: {formatted_similarities[:5]}")

            novelty_metadata["max_similarity"] = max_similarity
            novelty_metadata["similarity_scores"] = similarity_scores

            if max_similarity <= self.similarity_threshold:
                logger.info(
                    f"NOVELTY CHECK {attempt + 1}/{self.max_novelty_attempts}: "
                    f"Accepting program due to low similarity "
                    f"({max_similarity:.3f} <= {self.similarity_threshold})"
                )
                return True, novelty_metadata

            # High similarity detected - check with LLM if configured
            should_reject = True
            novelty_cost = 0.0

            # Check if any LLM check is possible (either legacy or agentic)
            can_check = (self.llm is not None) or (self.agentic_mode and self.agent_runner)
            
            if can_check:
                # Get the most similar program for LLM comparison
                most_similar_program = database.get_most_similar_program(
                    code_embedding, parent_program.island_idx,
                    exclude_program_id=exclude_id
                )

                if most_similar_program:
                    try:
                        is_novel, explanation, cost = self.check_llm_novelty(
                            proposed_text, most_similar_program
                        )
                        should_reject = not is_novel
                        novelty_cost = cost
                        novelty_metadata["novelty_checks_performed"] += 1
                        novelty_metadata["novelty_total_cost"] += cost
                        novelty_metadata["novelty_explanation"] = explanation
                    except Exception as e:
                        logger.warning(f"Error reading code for novelty check: {e}")
                        should_reject = True  # Default to rejection on error

            if should_reject:
                logger.info(
                    f"NOVELTY CHECK {attempt + 1}/{self.max_novelty_attempts}: "
                    f"Rejecting program due to high similarity "
                    f"({max_similarity:.3f} > {self.similarity_threshold})"
                    + (
                        f" and LLM novelty check (cost: {novelty_cost:.4f})"
                        if novelty_cost > 0
                        else ""
                    )
                    + ". Retrying with different parent/inspirations."
                )
                # Continue to next attempt (rejection sampling)
                continue
            else:
                logger.info(
                    f"NOVELTY CHECK {attempt + 1}/{self.max_novelty_attempts}: "
                    f"Accepting program despite high similarity "
                    f"({max_similarity:.3f} > {self.similarity_threshold}) "
                    f"due to LLM novelty check (cost: {novelty_cost:.4f})."
                )
                return True, novelty_metadata

        # All attempts exhausted, reject the program
        logger.info(
            f"NOVELTY CHECK: Exhausted all {self.max_novelty_attempts} attempts, "
            "rejecting program."
        )
        return False, novelty_metadata

    def check_llm_novelty(
        self, 
        proposed_code: str, 
        most_similar_program: Program
    ) -> Tuple[bool, str, float]:
        """
        Use LLM to judge if the proposed code is meaningfully different from
        the most similar program.

        Args:
            proposed_code: The newly generated code
            most_similar_program: The most similar existing program

        Returns:
            Tuple of (is_novel, explanation, api_cost)
        """
        original_code = most_similar_program.code
        if (not original_code) and self.code_loader is not None:
            try:
                original_code = self.code_loader(most_similar_program)
            except Exception as e:
                logger.warning(f"Failed to load code for novelty check: {e}")
                original_code = most_similar_program.code

        # In agentic mode, use the agent runner if available
        if self.agentic_mode and self.agent_runner and self.agent_config:
            return self._check_llm_novelty_agentic(original_code, proposed_code)

        if self.llm is None:
            logger.debug("Novelty LLM not configured, skipping novelty check")
            return True, "No novelty LLM configured", 0.0

        # Legacy LLMClient path
        user_msg = NOVELTY_USER_MSG.format(
            language=self.language,
            existing_code=original_code,
            proposed_code=proposed_code,
        )

        try:
            response = self.llm.query(
                msg=user_msg,
                system_msg=NOVELTY_SYSTEM_MSG,
                llm_kwargs=self.llm.get_kwargs(),
            )

            if response is None or response.content is None:
                logger.warning("Novelty LLM returned empty response")
                return True, "LLM response was empty", 0.0

            content = response.content.strip()
            api_cost = response.cost or 0.0

            return self._parse_novelty_response(content, api_cost)

        except Exception as e:
            logger.error(f"Error in novelty LLM check: {e}")
            if self.error_accepts:
                return True, f"Error (accepting): {e}", 0.0
            else:
                return False, f"Error (rejecting): {e}", 0.0

    def _check_llm_novelty_agentic(
        self,
        original_code: str,
        new_code: str,
    ) -> Tuple[bool, str, float]:
        """Agentic backend implementation of novelty check."""
        system_msg = NOVELTY_SYSTEM_MSG
        # Format user message with correct template keys
        user_msg = NOVELTY_USER_MSG.format(
            language=self.language,
            existing_code=original_code,
            proposed_code=new_code,
        )
        
        cost = 0.0
        response_text = ""
        
        with tempfile.TemporaryDirectory() as tmp_dir:
            workdir = Path(tmp_dir)
            
            try:
                # Run the task
                events = self.agent_runner(
                    user_prompt=user_msg,
                    system_prompt=system_msg,
                    workdir=workdir,
                    profile=self.agent_config.cli_profile,
                    sandbox=self.agent_config.sandbox,
                    approval_mode="full-auto", 
                    max_seconds=60,
                    max_events=20,
                    extra_cli_config=self.agent_config.extra_cli_config,
                    cli_path=self.agent_config.cli_path,
                    session_kind="novelty_judge"
                )
                
                for event in events:
                    if event.get("type") == "agent_message":
                        text = event.get("item", {}).get("text", "")
                        if text:
                            response_text += text
                    elif event.get("type") == "usage":
                        usage = event.get("usage", {})
                        if "total_cost_usd" in usage:
                            cost += float(usage["total_cost_usd"])
            
            except Exception as e:
                logger.warning(f"Agentic novelty check failed: {e}")
                if self.error_accepts:
                    return True, f"Agentic check failed ({e}), defaulting to accept.", 0.0
                else:
                    return False, f"Agentic check failed ({e}), defaulting to reject.", 0.0

        response_content = response_text.strip()
        return self._parse_novelty_response(response_content, cost)

    def _parse_novelty_response(self, content: str, cost: float) -> Tuple[bool, str, float]:
        """Parse LLM response for novelty decision."""
        # Normalize content
        content_upper = content.upper()
        
        # Look for YES/NO or NOVEL keyword
        is_novel = (
            content_upper.startswith("NOVEL") or 
            content_upper.startswith("**NOVEL**") or
            "YES" in content_upper.split()[:5] # Heuristic: YES in first 5 words
        )
        
        # If response starts with NOT NOVEL or NO, it's not novel
        if (
            content_upper.startswith("NOT NOVEL") or 
            content_upper.startswith("**NOT NOVEL**") or
            "NO" in content_upper.split()[:5]
        ):
            is_novel = False
            
        explanation = content
        return is_novel, explanation, cost

    def log_novelty_skip_message(self, reason: str) -> None:
        """Log a message about skipping novelty check."""
        logger.info(f"NOVELTY CHECK: Skipping rejection sampling - {reason}")