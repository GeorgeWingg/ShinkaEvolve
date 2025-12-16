from typing import Any, Callable, Dict, Iterator, List, Optional, Tuple
import logging
import json
import shutil
import tempfile
import uuid
from pathlib import Path
from shinka.database import Program
from shinka.llm import LLMClient
from shinka.prompts import (
    construct_individual_program_msg,
    # Non-agentic mode prompts (legacy)
    META_STEP1_SYSTEM_MSG,
    META_STEP1_USER_MSG,
    META_STEP2_SYSTEM_MSG,
    META_STEP2_USER_MSG,
    META_STEP3_SYSTEM_MSG,
    META_STEP3_USER_MSG,
    # Agentic mode prompts (new - agent explores results directory)
    AGENTIC_META_SYSTEM_MSG,
    AGENTIC_META_STEP1_USER_MSG,
    AGENTIC_META_STEP2_USER_MSG,
    AGENTIC_META_STEP3_USER_MSG,
)

logger = logging.getLogger(__name__)


class MetaSummarizer:
    """Handles meta-level summarization and recommendation generation.

    Supports two modes:
    1. Non-agentic (legacy): Uses LLMClient with piped-in code/metrics
    2. Agentic: Agent runs in results_dir and explores files directly
    """

    def __init__(
        self,
        meta_llm_client: Optional[LLMClient] = None,
        agent_runner: Optional[Callable[..., Iterator[Dict[str, Any]]]] = None,
        agent_workdir: Optional[Path] = None,
        results_dir: Optional[Path] = None,
        language: str = "python",
        use_text_feedback: bool = False,
        max_recommendations: int = 5,
        agentic_mode: bool = False,
    ):
        self.meta_llm_client = meta_llm_client
        self.agent_runner = agent_runner
        # For non-agentic mode: temp workdir for agent queries
        self.agent_workdir = agent_workdir or Path(tempfile.gettempdir()) / "shinka_meta_queries"
        # For agentic mode: results directory where agent explores
        self.results_dir = results_dir
        self.language = language
        self.use_text_feedback = use_text_feedback
        self.max_recommendations = max_recommendations
        self.agentic_mode = agentic_mode

        # Meta state
        self.meta_summary = None
        self.meta_scratch_pad = None  # Global insights scratchpad
        self.meta_recommendations = None
        self.meta_recommendations_history = []

        # Track programs evaluated since last meta query for persistent memory
        self.evaluated_since_last_meta: List[Program] = []

        # Track the accumulated count of programs processed in meta updates
        self.total_programs_processed = 0

    def set_results_dir(self, results_dir: Path) -> None:
        """Set the results directory (called after runner initializes it)."""
        self.results_dir = results_dir
        logger.debug(f"MetaSummarizer results_dir set to: {results_dir}")

    def add_evaluated_program(self, program: Program) -> None:
        """Add newly evaluated program to the tracking list."""
        logger.debug(
            f"Evaluating program {program.id} for meta memory: "
            f"correct={program.correct}"
        )

        # Track ALL evaluated programs (both correct and incorrect)
        # for meta learning
        self.evaluated_since_last_meta.append(program)
        logger.info(
            f"Added program {program.id} to meta memory tracking "
            f"(correct={program.correct}, "
            f"total: {len(self.evaluated_since_last_meta)})"
        )

        # Log when we're getting close to the meta update threshold
        if hasattr(self, "_last_logged_count"):
            if len(self.evaluated_since_last_meta) != self._last_logged_count:
                logger.debug(
                    f"Meta memory: {len(self.evaluated_since_last_meta)} "
                    f"programs tracked"
                )
        self._last_logged_count = len(self.evaluated_since_last_meta)

    def should_update_meta(self, meta_rec_interval: Optional[int]) -> bool:
        """Check if meta update should be performed based on interval.

        Now triggers based on the number of unprocessed programs rather than
        generation intervals for better timing with parallel jobs.
        """
        # Need either meta_llm_client or agent_runner to perform updates
        if meta_rec_interval is None or (not self.meta_llm_client and not self.agent_runner):
            return False

        # Use number of unprocessed programs instead of generation count
        unprocessed_count = len(self.evaluated_since_last_meta)
        return unprocessed_count >= meta_rec_interval

    def _query_via_agent(
        self, user_msg: str, system_msg: str
    ) -> Tuple[Optional[str], float]:
        """Query using CLI backend (agent_runner), extract text from events.

        This is the LEGACY non-agentic mode that runs in a temp directory.
        For agentic mode, use _query_via_agent_agentic() instead.

        Args:
            user_msg: The user prompt to send
            system_msg: The system prompt/instructions

        Returns:
            Tuple of (response_text, cost). Cost is always 0.0 since
            agent_runner doesn't provide cost tracking.
        """
        if not self.agent_runner:
            return None, 0.0

        # Ensure base workdir exists
        self.agent_workdir.mkdir(parents=True, exist_ok=True)

        # Create unique workdir for this query
        query_workdir = self.agent_workdir / str(uuid.uuid4())
        query_workdir.mkdir(parents=True, exist_ok=True)

        try:
            response_text = ""
            for event in self.agent_runner(
                user_prompt=user_msg,
                workdir=query_workdir,
                system_prompt=system_msg,
                profile=None,
                sandbox="",
                approval_mode="full-auto",
                max_seconds=300,
                max_events=100,
                extra_cli_config={},
                session_kind="meta",
            ):
                # Extract text from agent_message events
                if event.get("type") == "agent_message":
                    item = event.get("item", {})
                    text = item.get("text", "")
                    if text:
                        response_text += text

            if response_text:
                logger.debug(f"Agent query returned {len(response_text)} chars")
                return response_text.strip(), 0.0
            else:
                logger.warning("Agent query returned empty response")
                return None, 0.0

        except Exception as e:
            logger.error(f"Agent query failed: {e}")
            return None, 0.0

        finally:
            # Clean up query workdir
            try:
                shutil.rmtree(query_workdir, ignore_errors=True)
            except Exception:
                pass

    def _query_via_agent_agentic(
        self, user_msg: str, system_msg: str, output_file: Optional[str] = None
    ) -> Tuple[Optional[str], float]:
        """Query using CLI backend in AGENTIC mode - agent runs in results_dir.

        The agent explores the results directory, reads gen_*/results/metrics.json
        and the full gen_*/ workspace, and writes output to _meta/ directory.

        Args:
            user_msg: The user prompt (tells agent what to analyze and where to write)
            system_msg: The system prompt/instructions
            output_file: Optional path relative to results_dir to read output from
                        (e.g., "_meta/insights.md")

        Returns:
            Tuple of (response_text, cost). Response is read from output_file if specified,
            otherwise extracted from agent events.
        """
        if not self.agent_runner:
            return None, 0.0

        if not self.results_dir or not self.results_dir.exists():
            logger.error(f"Agentic meta: results_dir not set or doesn't exist: {self.results_dir}")
            return None, 0.0

        # Ensure _meta directory exists
        meta_dir = self.results_dir / "_meta"
        meta_dir.mkdir(parents=True, exist_ok=True)
        (meta_dir / "summaries").mkdir(parents=True, exist_ok=True)

        try:
            logger.info(f"Agentic meta: Running agent in {self.results_dir}")

            # Run agent in results directory
            for event in self.agent_runner(
                user_prompt=user_msg,
                workdir=self.results_dir,
                system_prompt=system_msg,
                profile=None,
                sandbox="workspace-write",  # Agent can read gen_*/ and write to _meta/
                approval_mode="full-auto",
                max_seconds=600,  # Give more time for exploration
                max_events=200,
                extra_cli_config={},
                session_kind="meta",
            ):
                # Log progress
                if event.get("type") == "agent_message":
                    item = event.get("item", {})
                    text = item.get("text", "")
                    if text:
                        logger.debug(f"Agentic meta agent: {text[:100]}...")

            # Read output from file if specified
            if output_file:
                output_path = self.results_dir / output_file
                if output_path.exists():
                    content = output_path.read_text(encoding="utf-8")
                    logger.info(f"Agentic meta: Read {len(content)} chars from {output_file}")
                    return content.strip(), 0.0
                else:
                    logger.warning(f"Agentic meta: Output file not found: {output_path}")
                    return None, 0.0
            else:
                # No output file specified - just return success indicator
                return "OK", 0.0

        except Exception as e:
            logger.error(f"Agentic meta query failed: {e}")
            import traceback
            logger.debug(f"Traceback: {traceback.format_exc()}")
            return None, 0.0

    def _read_meta_file(self, relative_path: str) -> Optional[str]:
        """Read a file from the _meta directory."""
        if not self.results_dir:
            return None
        file_path = self.results_dir / relative_path
        if file_path.exists():
            return file_path.read_text(encoding="utf-8")
        return None

    def _read_all_summaries(self) -> Optional[str]:
        """Read all individual summaries from _meta/summaries/."""
        if not self.results_dir:
            return None
        summaries_dir = self.results_dir / "_meta" / "summaries"
        if not summaries_dir.exists():
            return None

        summaries = []
        for summary_file in sorted(summaries_dir.glob("gen_*.md")):
            content = summary_file.read_text(encoding="utf-8")
            summaries.append(content)

        if summaries:
            return "\n\n---\n\n".join(summaries)
        return None

    def update_meta_memory(
        self, best_program: Optional[Program] = None
    ) -> Tuple[Optional[str], float]:
        """
        Perform 3-step meta-analysis and update internal state.
        Returns tuple of (updated_recommendations, total_cost) or
        (None, 0.0) if no update occurred.

        In agentic mode, the agent explores results_dir and writes to _meta/.
        In non-agentic mode, code/metrics are piped into the LLM.
        """
        if not self.meta_llm_client and not self.agent_runner:
            logger.warning("No meta LLM client or agent_runner configured")
            return None, 0.0

        # Use recently evaluated programs for memory scratchpad
        programs_to_analyze = (
            self.evaluated_since_last_meta if self.evaluated_since_last_meta else []
        )

        if len(programs_to_analyze) == 0:
            logger.info("No programs evaluated since last meta query, skipping")
            return None, 0.0

        total_meta_cost = 0.0

        # Use agentic mode if enabled and we have both agent_runner and results_dir
        use_agentic = (
            self.agentic_mode
            and self.agent_runner
            and self.results_dir
            and self.results_dir.exists()
        )

        if use_agentic:
            logger.info("==> Meta-analysis using AGENTIC mode (agent explores results_dir)")
            return self._update_meta_memory_agentic(programs_to_analyze, best_program)

        # Non-agentic mode (legacy)
        try:
            # Step 1: Create individual program summaries
            individual_summaries, step1_cost = self._step1_individual_summaries(
                programs_to_analyze
            )
            total_meta_cost += step1_cost
            if not individual_summaries:
                logger.error("Step 1 failed - no individual summaries generated")
                return None, total_meta_cost

            # Step 2: Generate global insights scratchpad
            global_insights, step2_cost = self._step2_global_insights(
                individual_summaries, best_program
            )
            total_meta_cost += step2_cost
            if not global_insights:
                logger.error("Step 2 failed - no global insights generated")
                return None, total_meta_cost

            # Step 3: Generate recommendations based on insights
            recommendations, step3_cost = self._step3_generate_recommendations(
                global_insights, best_program
            )
            total_meta_cost += step3_cost
            if not recommendations:
                logger.error("Step 3 failed - no recommendations generated")
                return None, total_meta_cost

            # Update internal state
            # Concatenate new individual summaries to existing ones
            if self.meta_summary:
                self.meta_summary += "\n\n" + individual_summaries
            else:
                self.meta_summary = individual_summaries

            self.meta_scratch_pad = global_insights
            self.meta_recommendations = recommendations

            # Store the newly generated recommendations in history immediately
            if recommendations and isinstance(recommendations, str):
                self.meta_recommendations_history.append(recommendations)
                logger.debug(
                    f"Added new recommendations to history "
                    f"(total: {len(self.meta_recommendations_history)})"
                )

            logger.info(
                f"==> Meta-analysis completed successfully with 3-step process (total cost: ${total_meta_cost:.4f})"
            )
        except Exception as e:
            logger.error(f"Failed to complete 3-step meta-analysis: {e}")
            return None, total_meta_cost

        # Clear the evaluated programs list immediately after processing
        # This ensures that only programs added AFTER this meta update
        # will be saved as "unprocessed" programs
        num_processed = len(self.evaluated_since_last_meta)
        self.total_programs_processed += num_processed
        self.evaluated_since_last_meta = []
        logger.info(
            f"Processed and cleared {num_processed} programs from meta memory "
            f"(total processed: {self.total_programs_processed})"
        )

        return (
            (
                self.meta_recommendations
                if isinstance(self.meta_recommendations, str)
                else None
            ),
            total_meta_cost,
        )

    def _update_meta_memory_agentic(
        self, programs_to_analyze: List[Program], best_program: Optional[Program] = None
    ) -> Tuple[Optional[str], float]:
        """
        Agentic mode meta-analysis: agent explores results_dir and writes to _meta/.

        The agent reads gen_*/results/metrics.json and the full gen_*/ workspace,
        then writes summaries, insights, and recommendations to _meta/.
        """
        total_cost = 0.0

        # Determine generation range to analyze
        generations = sorted(set(p.generation for p in programs_to_analyze))
        if not generations:
            logger.warning("No generations to analyze")
            return None, 0.0

        start_gen = min(generations)
        end_gen = max(generations)

        # Get best program info
        best_gen = best_program.generation if best_program else end_gen
        best_score = best_program.combined_score if best_program else 0.0

        try:
            # Step 1: Agent analyzes programs and writes to _meta/summaries/
            logger.info(f"==> Agentic Step 1: Analyzing generations {start_gen}-{end_gen}")
            user_msg = AGENTIC_META_STEP1_USER_MSG.format(
                start_gen=start_gen,
                end_gen=end_gen,
            )
            result, cost = self._query_via_agent_agentic(
                user_msg, AGENTIC_META_SYSTEM_MSG, output_file=None
            )
            total_cost += cost
            if result is None:
                logger.error("Agentic Step 1 failed")
                return None, total_cost

            # Step 2: Agent generates global insights
            logger.info(f"==> Agentic Step 2: Generating global insights (best: gen {best_gen})")
            user_msg = AGENTIC_META_STEP2_USER_MSG.format(
                best_gen=best_gen,
                best_score=f"{best_score:.4f}",
            )
            result, cost = self._query_via_agent_agentic(
                user_msg, AGENTIC_META_SYSTEM_MSG, output_file="_meta/insights.md"
            )
            total_cost += cost
            if result is None:
                logger.error("Agentic Step 2 failed - no insights generated")
                return None, total_cost
            global_insights = result

            # Step 3: Agent generates recommendations
            logger.info(f"==> Agentic Step 3: Generating recommendations")
            user_msg = AGENTIC_META_STEP3_USER_MSG.format(
                best_gen=best_gen,
                best_score=f"{best_score:.4f}",
                max_recommendations=self.max_recommendations,
            )
            result, cost = self._query_via_agent_agentic(
                user_msg, AGENTIC_META_SYSTEM_MSG, output_file="_meta/recommendations.md"
            )
            total_cost += cost
            if result is None:
                logger.error("Agentic Step 3 failed - no recommendations generated")
                return None, total_cost
            recommendations = result

            # Update internal state from files
            all_summaries = self._read_all_summaries()
            if all_summaries:
                if self.meta_summary:
                    self.meta_summary += "\n\n" + all_summaries
                else:
                    self.meta_summary = all_summaries

            self.meta_scratch_pad = global_insights
            self.meta_recommendations = recommendations

            if recommendations:
                self.meta_recommendations_history.append(recommendations)
                logger.debug(
                    f"Added recommendations to history "
                    f"(total: {len(self.meta_recommendations_history)})"
                )

            logger.info(
                f"==> Agentic meta-analysis completed (cost: ${total_cost:.4f})"
            )

        except Exception as e:
            logger.error(f"Agentic meta-analysis failed: {e}")
            import traceback
            logger.debug(f"Traceback: {traceback.format_exc()}")
            return None, total_cost

        # Clear processed programs
        num_processed = len(self.evaluated_since_last_meta)
        self.total_programs_processed += num_processed
        self.evaluated_since_last_meta = []
        logger.info(
            f"Processed and cleared {num_processed} programs "
            f"(total: {self.total_programs_processed})"
        )

        return (
            self.meta_recommendations if isinstance(self.meta_recommendations, str) else None,
            total_cost,
        )

    def get_unprocessed_program_count(self) -> int:
        """Get the count of unprocessed programs awaiting meta analysis."""
        return len(self.evaluated_since_last_meta)

    def get_recommendations_history_count(self) -> int:
        """Get the count of previous recommendations stored in history."""
        return len(self.meta_recommendations_history)

    def get_total_programs_processed(self) -> int:
        """Get the total count of programs processed in meta updates."""
        return self.total_programs_processed

    def perform_final_summary(
        self, results_dir: str, best_program: Optional[Program] = None
    ) -> bool:
        """Perform a final meta summary if there are unprocessed programs."""
        if not self.meta_llm_client:
            logger.info("No meta LLM client configured, skipping final summary")
            return False

        unprocessed_count = len(self.evaluated_since_last_meta)
        if unprocessed_count == 0:
            logger.info("No unprocessed programs for final summary")
            return False

        logger.info(
            f"Performing final meta summary for {unprocessed_count} "
            f"remaining programs..."
        )

        updated_recs, meta_cost = self.update_meta_memory(best_program)
        if updated_recs:
            self.write_meta_output(results_dir)
            logger.info(f"Final meta summary completed (cost: ${meta_cost:.4f})")
            return True
        else:
            logger.warning("Final meta summary failed to generate recommendations")
            return False

    def _step1_individual_summaries(
        self, programs_to_analyze: List[Program]
    ) -> Tuple[Optional[str], float]:
        """Step 1: Create individual summaries for each program.

        Uses agent_runner (CLI backend) if available, otherwise falls back
        to LLMClient batch queries.
        """
        if not programs_to_analyze:
            logger.warning("No programs to analyze in Step 1")
            return None, 0.0

        num_programs = len(programs_to_analyze)

        # Use agent_runner if available (sequential processing)
        if self.agent_runner:
            logger.info(f"==> Step 1 - Processing {num_programs} programs via agent_runner")
            combined_summaries = []
            total_cost = 0.0

            for program in programs_to_analyze:
                individual_program_msg = construct_individual_program_msg(
                    program,
                    language=self.language,
                    include_text_feedback=self.use_text_feedback,
                )
                user_msg = META_STEP1_USER_MSG.replace(
                    "{individual_program_msg}", individual_program_msg
                )

                response, cost = self._query_via_agent(user_msg, META_STEP1_SYSTEM_MSG)
                total_cost += cost

                if response:
                    program_summary = response.strip()
                    patch_name = program.metadata.get("patch_name", "unknown")
                    program_summary += "\n**Program Identifier:** "
                    program_summary += f"Generation {program.generation} - Patch Name {patch_name} - Correct Program: {program.correct}"
                    combined_summaries.append((program.generation, program_summary))
                else:
                    logger.warning(f"Step 1: Empty response for program {program.id}")

            # Sort by generation
            combined_summaries.sort(key=lambda x: x[0])
            summaries_only = [s for _, s in combined_summaries]

            if not summaries_only:
                logger.error("Step 1: No valid summaries generated via agent_runner")
                return None, total_cost

            final_summary = "\n\n".join(summaries_only)
            logger.info(
                f"==> Step 1 - {len(summaries_only)}/{num_programs} "
                f"individual summaries generated via agent_runner"
            )
            return final_summary, total_cost

        # Fall back to LLMClient batch queries
        if not self.meta_llm_client:
            logger.error("Step 1: No meta_llm_client or agent_runner available")
            return None, 0.0

        # Create individual program messages for batch processing
        user_messages, generation_ids, patch_names, correct_programs = [], [], [], []
        for program in programs_to_analyze:
            individual_program_msg = construct_individual_program_msg(
                program,
                language=self.language,
                include_text_feedback=self.use_text_feedback,
            )
            generation_ids.append(program.generation)
            patch_names.append(program.metadata.get("patch_name", "unknown"))
            correct_programs.append(program.correct)
            user_msg = META_STEP1_USER_MSG.replace(
                "{individual_program_msg}", individual_program_msg
            )
            user_messages.append(user_msg)

        # Use batch query to process all programs
        logger.info(f"==> Step 1 - Processing {num_programs} programs with batch query")
        responses = self.meta_llm_client.batch_kwargs_query(
            num_samples=num_programs,
            msg=user_messages,
            system_msg=META_STEP1_SYSTEM_MSG,
        )

        if not responses:
            logger.error("Step 1: Failed to get responses from meta LLM client")
            return None, 0.0

        # Filter out None responses and combine summaries
        valid_responses = [r for r in responses if r is not None]
        if not valid_responses:
            logger.error("Step 1: All batch responses were None")
            return None, 0.0

        # Combine all individual summaries
        combined_summaries = []
        total_cost = 0.0
        for i, response in enumerate(valid_responses):
            if response and response.content:
                program_summary = response.content.strip()
                program_summary += "\n**Program Identifier:** "
                program_summary += f"Generation {generation_ids[i]} - Patch Name {patch_names[i]} - Correct Program: {correct_programs[i]}"
                combined_summaries.append(program_summary)
                total_cost += response.cost or 0.0
            else:
                logger.warning(f"Step 1: Empty response for program {i}")

        # Sort combined_summaries by generation (using generation_ids)
        # Zip together summaries and their generation, sort, then extract summaries
        summaries_with_gen = list(zip(generation_ids, combined_summaries))
        summaries_with_gen.sort(key=lambda x: x[0])
        combined_summaries = [summary for _, summary in summaries_with_gen]

        if not combined_summaries:
            logger.error("Step 1: No valid summaries generated")
            return None, total_cost

        # Join all summaries with double newlines
        final_summary = "\n\n".join(combined_summaries)
        logger.info(
            f"==> Step 1 - {len(combined_summaries)}/{num_programs} "
            f"individual summaries generated (cost: ${total_cost:.4f})"
        )
        return final_summary, total_cost

    def _step2_global_insights(
        self, individual_summaries: str, best_program: Optional[Program] = None
    ) -> Tuple[Optional[str], float]:
        """Step 2: Generate global insights from individual summaries.

        Uses agent_runner if available, otherwise falls back to LLMClient.
        """
        previous_insights = self.meta_scratch_pad or "*No previous insights available.*"

        # Format best program information
        if best_program:
            best_program_info = construct_individual_program_msg(
                best_program,
                language=self.language,
                include_text_feedback=self.use_text_feedback,
            )
        else:
            best_program_info = "*No best program information available.*"

        user_msg = (
            META_STEP2_USER_MSG.replace("{individual_summaries}", individual_summaries)
            .replace("{previous_insights}", previous_insights)
            .replace("{best_program_info}", best_program_info)
        )

        # Use agent_runner if available
        if self.agent_runner:
            logger.info("==> Step 2 - Generating global insights via agent_runner")
            response, cost = self._query_via_agent(user_msg, META_STEP2_SYSTEM_MSG)
            if response:
                logger.info("==> Step 2 - Global insights generated via agent_runner")
                return response, cost
            else:
                logger.error("Step 2: Failed to get response from agent_runner")
                return None, cost

        # Fall back to LLMClient
        if not self.meta_llm_client:
            logger.error("Step 2: No meta_llm_client or agent_runner available")
            return None, 0.0

        llm_params = self.meta_llm_client.get_kwargs()
        response = self.meta_llm_client.query(
            msg=user_msg,
            system_msg=META_STEP2_SYSTEM_MSG,
            llm_kwargs=llm_params,
        )

        if response is None:
            logger.error("Step 2: Failed to get response from meta LLM client")
            return None, 0.0

        cost = response.cost or 0.0
        logger.info(f"==> Step 2 - Global insights generated (cost: ${cost:.4f})")
        return response.content.strip(), cost

    def _step3_generate_recommendations(
        self, global_insights: str, best_program: Optional[Program] = None
    ) -> Tuple[Optional[str], float]:
        """Step 3: Generate recommendations based on global insights.

        Uses agent_runner if available, otherwise falls back to LLMClient.
        """
        previous_recommendations = (
            self.meta_recommendations or "*No previous recommendations available.*"
        )

        # Format best program information
        if best_program:
            best_program_info = construct_individual_program_msg(
                best_program,
                language=self.language,
                include_text_feedback=self.use_text_feedback,
            )
        else:
            best_program_info = "*No best program information available.*"

        user_msg = (
            META_STEP3_USER_MSG.replace("{global_insights}", global_insights)
            .replace("{previous_recommendations}", previous_recommendations)
            .replace("{max_recommendations}", str(self.max_recommendations))
            .replace("{best_program_info}", best_program_info)
        )

        # Use agent_runner if available
        if self.agent_runner:
            logger.info("==> Step 3 - Generating recommendations via agent_runner")
            response, cost = self._query_via_agent(user_msg, META_STEP3_SYSTEM_MSG)
            if response:
                logger.info("==> Step 3 - Recommendations generated via agent_runner")
                return response, cost
            else:
                logger.error("Step 3: Failed to get response from agent_runner")
                return None, cost

        # Fall back to LLMClient
        if not self.meta_llm_client:
            logger.error("Step 3: No meta_llm_client or agent_runner available")
            return None, 0.0

        llm_params = self.meta_llm_client.get_kwargs()
        response = self.meta_llm_client.query(
            msg=user_msg,
            system_msg=META_STEP3_SYSTEM_MSG,
            llm_kwargs=llm_params,
        )

        if response is None:
            logger.error("Step 3: Failed to get response from meta LLM client")
            return None, 0.0

        cost = response.cost or 0.0
        logger.info(f"==> Step 3 - Recommendations generated (cost: ${cost:.4f})")
        return response.content.strip(), cost

    def get_current(
        self,
    ) -> Tuple[Optional[str], Optional[str], Optional[str]]:
        """Get current meta recommendations without updating."""
        recommendations = (
            self.meta_recommendations
            if isinstance(self.meta_recommendations, str)
            else None
        )
        summary = self.meta_summary if isinstance(self.meta_summary, str) else None
        scratch_pad = (
            self.meta_scratch_pad if isinstance(self.meta_scratch_pad, str) else None
        )

        # Debug logging
        logger.debug(
            f"get_current() returning: "
            f"recommendations={'Yes' if recommendations else 'No'}, "
            f"summary={'Yes' if summary else 'No'}, "
            f"scratch_pad={'Yes' if scratch_pad else 'No'}"
        )
        if recommendations:
            rec_preview = (
                recommendations[:100] + "..."
                if len(recommendations) > 100
                else recommendations
            )
            logger.debug(f"Current recommendations preview: {rec_preview}")

        return (recommendations, summary, scratch_pad)

    def _build_previous_context(self) -> str:
        """Build context string from previous meta state."""
        context_parts = []

        if self.meta_summary and self.meta_summary != "none":
            context_parts.append("## Previous Summary")
            context_parts.append(str(self.meta_summary))

        if self.meta_recommendations and self.meta_recommendations != "none":
            rec_count = self._count_recommendations(self.meta_recommendations)
            context_parts.append(
                f"\n## Previous Recommendations "
                f"({rec_count}/{self.max_recommendations} items)"
            )
            context_parts.append(str(self.meta_recommendations))

        if not context_parts:
            return "*No previous memory state - this is the first meta update.*"

        return "\n".join(context_parts)

    def _count_recommendations(self, text: str) -> int:
        """Count recommendation items (lines starting with •)."""
        if not text:
            return 0
        return len([line for line in text.split("\n") if line.strip().startswith("•")])

    def save_meta_state(self, filepath: str) -> None:
        """Save the meta state to a file.

        Only saves:
        1. Current meta state (summary, scratchpad, recommendations)
        2. Unprocessed programs that haven't been summarized yet
        """
        try:
            # Only serialize unprocessed programs (those added since last meta update)
            unprocessed_programs_data = []
            failed_serializations = 0

            for i, prog in enumerate(self.evaluated_since_last_meta):
                try:
                    prog_dict = prog.to_dict()
                    unprocessed_programs_data.append(prog_dict)
                except Exception as e:
                    prog_id = prog.id if hasattr(prog, "id") else "unknown"
                    logger.warning(f"Failed to serialize program {i} ({prog_id}): {e}")
                    failed_serializations += 1

            meta_data = {
                "unprocessed_programs": unprocessed_programs_data,
                "meta_summary": self.meta_summary,
                "meta_scratch_pad": self.meta_scratch_pad,
                "meta_recommendations": self.meta_recommendations,
                "meta_recommendations_history": (self.meta_recommendations_history),
                "total_programs_meta_processed": self.total_programs_processed,
            }

            # Ensure directory exists
            filepath_obj = Path(filepath)
            filepath_obj.parent.mkdir(parents=True, exist_ok=True)
            # Write to temporary file first, then rename for atomic operation
            temp_filepath = filepath_obj.with_suffix(".tmp")

            with open(temp_filepath, "w", encoding="utf-8") as f:
                json.dump(meta_data, f, indent=2, default=str)

            # Atomic rename
            temp_filepath.replace(filepath_obj)

            saved_count = len(unprocessed_programs_data)

            logger.info(
                f"Saved meta state to {filepath}: "
                f"{saved_count} unprocessed programs, "
                f"summary: {'Yes' if self.meta_summary else 'No'}, "
                f"scratchpad: {'Yes' if self.meta_scratch_pad else 'No'}, "
                f"recommendations: {'Yes' if self.meta_recommendations else 'No'}, "
                f"history: {len(self.meta_recommendations_history)} items"
            )

            # Debug logging for what's being saved
            if self.meta_recommendations:
                rec_preview = (
                    self.meta_recommendations[:100] + "..."
                    if len(self.meta_recommendations) > 100
                    else self.meta_recommendations
                )
                logger.debug(f"Saving meta recommendations preview: {rec_preview}")
                logger.debug(
                    f"Saving meta recommendations length: "
                    f"{len(self.meta_recommendations)}"
                )
            else:
                logger.debug("No meta recommendations to save")

            # Debug: Log program IDs being saved
            if saved_count > 0:
                program_ids = [
                    prog.get("id", "no-id")[:8]
                    for prog in unprocessed_programs_data[:3]
                ]
                logger.debug(f"Sample unprocessed program IDs: {program_ids}...")

            if failed_serializations > 0:
                logger.warning(
                    f"Failed to serialize {failed_serializations} programs during save"
                )
        except Exception as e:
            logger.error(f"Failed to save meta state to {filepath}: {e}")
            import traceback

            logger.debug(f"Full traceback: {traceback.format_exc()}")
            # Clean up temp file if it exists
            temp_filepath = Path(filepath).with_suffix(".tmp")
            if temp_filepath.exists():
                try:
                    temp_filepath.unlink()
                except Exception:
                    pass

    def load_meta_state(self, filepath: str) -> bool:
        """Load the meta state from a file."""
        filepath_obj = Path(filepath)
        if not filepath_obj.exists():
            logger.info(f"No meta state file found at {filepath}")
            return False

        try:
            # Check file size and readability
            file_size = filepath_obj.stat().st_size
            if file_size == 0:
                logger.warning(f"Meta state file is empty: {filepath}")
                return False

            logger.info(f"Loading meta state from {filepath} (size: {file_size} bytes)")

            with open(filepath, "r", encoding="utf-8") as f:
                meta_data = json.load(f)

            # Validate the loaded data structure
            if not isinstance(meta_data, dict):
                logger.error(
                    f"Invalid meta state format: expected dict, got {type(meta_data)}"
                )
                return False

            # Support both old format (evaluated_programs) and new format
            # (unprocessed_programs)
            # for backward compatibility
            prog_list = meta_data.get("unprocessed_programs", [])
            if not prog_list and "evaluated_programs" in meta_data:
                # Backward compatibility: load from old format but warn
                prog_list = meta_data.get("evaluated_programs", [])
                logger.warning(
                    "Loading from old meta memory format with all evaluated programs"
                )

            prog_count = len(prog_list)
            logger.info(f"Meta state contains {prog_count} unprocessed programs")

            # Debug: Log the first program structure if available
            if prog_count > 0:
                logger.debug(
                    f"First program keys: "
                    f"{list(prog_list[0].keys()) if prog_list[0] else 'None'}"
                )

            # Restore evaluated programs with error handling
            restored_programs = []
            failed_programs = 0

            for i, prog_dict in enumerate(prog_list):
                try:
                    if not prog_dict:
                        logger.warning(f"Program {i} is None or empty")
                        failed_programs += 1
                        continue

                    if not isinstance(prog_dict, dict):
                        logger.warning(f"Program {i} is not a dict: {type(prog_dict)}")
                        failed_programs += 1
                        continue

                    # Check if required fields exist
                    required_fields = ["id", "code", "language", "generation"]
                    missing_fields = [f for f in required_fields if f not in prog_dict]
                    if missing_fields:
                        logger.warning(
                            f"Program {i} missing required fields: {missing_fields}"
                        )
                        failed_programs += 1
                        continue

                    program = Program.from_dict(prog_dict)
                    restored_programs.append(program)
                    logger.debug(f"Successfully restored program {i}: {program.id}")

                except Exception as e:
                    logger.warning(f"Failed to restore program {i}: {e}")
                    logger.debug(f"Program {i} data: {prog_dict}")
                    failed_programs += 1

            self.evaluated_since_last_meta = restored_programs

            if failed_programs > 0:
                logger.warning(
                    f"Failed to restore {failed_programs}/{prog_count} programs"
                )

            logger.info(
                f"Successfully restored {len(restored_programs)} "
                f"unprocessed programs to memory"
            )

            # Restore meta state
            self.meta_summary = meta_data.get("meta_summary")
            self.meta_scratch_pad = meta_data.get("meta_scratch_pad")
            self.meta_recommendations = meta_data.get("meta_recommendations")
            self.meta_recommendations_history = meta_data.get(
                "meta_recommendations_history", []
            )
            self.total_programs_processed = meta_data.get(
                "total_programs_meta_processed", 0
            )

            # Debug logging for meta recommendations
            if self.meta_recommendations:
                rec_preview = (
                    self.meta_recommendations[:100] + "..."
                    if len(self.meta_recommendations) > 100
                    else self.meta_recommendations
                )
                logger.debug(f"Loaded meta recommendations preview: {rec_preview}")
                logger.debug(
                    f"Meta recommendations length: {len(self.meta_recommendations)}"
                )
            else:
                logger.debug("No meta recommendations found in loaded data")

            logger.info(
                f"Successfully restored meta state: "
                f"{len(self.evaluated_since_last_meta)} unprocessed programs, "
                f"summary: {'Yes' if self.meta_summary else 'No'}, "
                f"scratchpad: {'Yes' if self.meta_scratch_pad else 'No'}, "
                f"recommendations: {'Yes' if self.meta_recommendations else 'No'}, "
                f"history: {len(self.meta_recommendations_history)} items"
            )
            return True

        except json.JSONDecodeError as e:
            logger.error(f"Invalid JSON in meta state file {filepath}: {e}")
            return False
        except Exception as e:
            logger.error(f"Failed to load meta state from {filepath}: {e}")
            import traceback

            logger.debug(f"Full traceback: {traceback.format_exc()}")
            return False

    def write_meta_output(self, results_dir: str) -> None:
        """Write meta summary, scratchpad, and recommendations to a file."""
        output_str = ""

        if self.meta_summary:
            output_str += "# INDIVIDUAL PROGRAM SUMMARIES\n\n"
            output_str += (
                "The following are summaries of individual programs "
                "evaluated since the last meta update:\n\n"
            )
            output_str += str(self.meta_summary)
            output_str += "\n\n"

        if self.meta_scratch_pad:
            output_str += "# GLOBAL INSIGHTS SCRATCHPAD\n\n"
            output_str += (
                "The following are global insights about optimization "
                "approaches and their effectiveness:\n\n"
            )
            output_str += str(self.meta_scratch_pad)
            output_str += "\n\n"

        if self.meta_recommendations:
            output_str += "# META RECOMMENDATIONS\n\n"
            output_str += (
                "The following are actionable recommendations for the next "
                "program generations:\n\n"
            )
            output_str += str(self.meta_recommendations)

        if output_str:
            meta_path = Path(results_dir) / f"meta_{self.total_programs_processed}.txt"
            with meta_path.open("w", encoding="utf-8") as f:
                f.write(output_str)
            logger.info(f"Wrote meta output to {meta_path}")
