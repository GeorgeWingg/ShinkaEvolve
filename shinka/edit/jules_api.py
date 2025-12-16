"""
Jules API Client for Google's cloud-based coding agent.

This module provides HTTP client functionality for the Jules REST API,
enabling programmatic access to Jules sessions for code editing tasks.

API Documentation: https://developers.google.com/jules/api
"""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass, field
from typing import Any, Dict, Iterator, List, Optional

import requests

logger = logging.getLogger(__name__)


# -----------------------------------------------------------------------------
# Exceptions
# -----------------------------------------------------------------------------


class JulesAPIError(RuntimeError):
    """Base exception for Jules API errors."""

    def __init__(self, message: str, status_code: Optional[int] = None):
        super().__init__(message)
        self.status_code = status_code


class JulesUnavailableError(JulesAPIError):
    """Raised when Jules API key is not configured or API is unavailable."""


class JulesQuotaExhaustedError(JulesAPIError):
    """Raised when Jules daily quota is exhausted (HTTP 429/403)."""


class JulesRepoNotConnectedError(JulesAPIError):
    """Raised when target repo is not connected in Jules UI."""


class JulesSessionError(JulesAPIError):
    """Raised when a Jules session fails."""


class JulesTimeoutError(JulesAPIError):
    """Raised when polling for session completion times out."""


# -----------------------------------------------------------------------------
# Data Classes
# -----------------------------------------------------------------------------


@dataclass
class JulesActivity:
    """Represents an activity/event within a Jules session."""

    type: str  # e.g., "planGenerated", "sessionCompleted", "messageSent"
    content: Dict[str, Any]
    timestamp: Optional[str] = None

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "JulesActivity":
        """Create JulesActivity from API response dict."""
        return cls(
            type=data.get("type", "unknown"),
            content=data.get("content", {}),
            timestamp=data.get("createTime"),
        )


@dataclass
class JulesSession:
    """Represents a Jules session."""

    id: str
    name: str  # Full resource name: "sessions/{id}"
    status: str  # PENDING, RUNNING, COMPLETED, FAILED, etc.
    prompt: str
    url: Optional[str] = None
    result_branch: Optional[str] = None
    pr_url: Optional[str] = None
    pr_number: Optional[int] = None
    error: Optional[str] = None
    activities: List[JulesActivity] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "JulesSession":
        """Create JulesSession from API response dict."""
        session_id = data.get("id", "")
        if not session_id and "name" in data:
            # Extract ID from name like "sessions/12345"
            session_id = data["name"].split("/")[-1]

        return cls(
            id=session_id,
            name=data.get("name", f"sessions/{session_id}"),
            status=data.get("state", data.get("status", "UNKNOWN")),
            prompt=data.get("prompt", ""),
            url=data.get("url"),
            result_branch=data.get("resultBranch"),
            pr_url=data.get("pullRequestUrl"),
            pr_number=data.get("pullRequestNumber"),
            error=data.get("error", {}).get("message") if data.get("error") else None,
        )

    @property
    def is_complete(self) -> bool:
        """Check if session has reached a terminal state."""
        return self.status in ("COMPLETED", "FAILED", "CANCELLED", "EXPIRED")

    @property
    def is_success(self) -> bool:
        """Check if session completed successfully."""
        return self.status == "COMPLETED"


# -----------------------------------------------------------------------------
# API Client
# -----------------------------------------------------------------------------


class JulesAPIClient:
    """HTTP client for Google Jules REST API.

    API Base: https://jules.googleapis.com/v1alpha/

    Authentication: Pass API key via X-Goog-Api-Key header.
    Get your API key from: https://jules.google.com/settings#api

    Usage:
        client = JulesAPIClient(api_key="your-key")
        sources = client.list_sources()
        session = client.create_session(
            prompt="Fix the bug in auth.py",
            github_repo="owner/repo",
            branch="main",
        )
        session = client.poll_until_complete(session.id)
    """

    BASE_URL = "https://jules.googleapis.com/v1alpha"

    def __init__(
        self,
        api_key: str,
        timeout: int = 60,
        max_retries: int = 3,
    ):
        """Initialize Jules API client.

        Args:
            api_key: Jules API key from jules.google.com/settings#api
            timeout: Request timeout in seconds
            max_retries: Max retries for transient failures
        """
        self.api_key = api_key
        self.timeout = timeout
        self.max_retries = max_retries
        self._session = requests.Session()
        self._session.headers.update(
            {
                "X-Goog-Api-Key": api_key,
                "Content-Type": "application/json",
            }
        )

    def _request(
        self,
        method: str,
        endpoint: str,
        json_data: Optional[Dict[str, Any]] = None,
        params: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Make an HTTP request to the Jules API.

        Args:
            method: HTTP method (GET, POST, etc.)
            endpoint: API endpoint (e.g., "/sessions")
            json_data: JSON body for POST requests
            params: Query parameters

        Returns:
            Parsed JSON response

        Raises:
            JulesQuotaExhaustedError: If quota is exceeded (429/403)
            JulesAPIError: For other API errors
        """
        url = f"{self.BASE_URL}{endpoint}"
        last_error: Optional[Exception] = None

        for attempt in range(self.max_retries):
            try:
                response = self._session.request(
                    method=method,
                    url=url,
                    json=json_data,
                    params=params,
                    timeout=self.timeout,
                )

                # Handle quota exhaustion
                if response.status_code in (429, 403):
                    error_data = response.json() if response.text else {}
                    error_msg = error_data.get("error", {}).get(
                        "message", "Quota exceeded"
                    )
                    raise JulesQuotaExhaustedError(
                        f"Jules quota exhausted: {error_msg}",
                        status_code=response.status_code,
                    )

                # Handle other errors
                if not response.ok:
                    error_data = response.json() if response.text else {}
                    error_msg = error_data.get("error", {}).get(
                        "message", response.reason
                    )
                    raise JulesAPIError(
                        f"Jules API error: {error_msg}",
                        status_code=response.status_code,
                    )

                return response.json() if response.text else {}

            except requests.exceptions.Timeout as e:
                last_error = e
                if attempt < self.max_retries - 1:
                    wait_time = 2**attempt
                    logger.warning(
                        f"Jules API timeout, retrying in {wait_time}s "
                        f"(attempt {attempt + 1}/{self.max_retries})"
                    )
                    time.sleep(wait_time)
                continue

            except requests.exceptions.RequestException as e:
                last_error = e
                if attempt < self.max_retries - 1:
                    wait_time = 2**attempt
                    logger.warning(
                        f"Jules API request failed: {e}, retrying in {wait_time}s "
                        f"(attempt {attempt + 1}/{self.max_retries})"
                    )
                    time.sleep(wait_time)
                continue

        raise JulesAPIError(f"Jules API request failed after {self.max_retries} retries: {last_error}")

    # -------------------------------------------------------------------------
    # Sources (Connected Repositories)
    # -------------------------------------------------------------------------

    def list_sources(self) -> List[str]:
        """List connected repositories (sources).

        Returns:
            List of source identifiers, e.g., ["sources/github/owner/repo", ...]

        Jules requires repositories to be "connected" in the Jules UI before
        they can be used via API. Use this to verify repo access.
        """
        response = self._request("GET", "/sources")
        sources = response.get("sources", [])
        return [s.get("name", "") for s in sources]

    def verify_repo_connected(self, github_repo: str) -> bool:
        """Verify a GitHub repo is connected in Jules.

        Args:
            github_repo: Repository in "owner/repo" format

        Returns:
            True if connected

        Raises:
            JulesRepoNotConnectedError: If repo is not connected
        """
        sources = self.list_sources()
        expected_source = f"sources/github/{github_repo}"

        if expected_source not in sources:
            raise JulesRepoNotConnectedError(
                f"Repository '{github_repo}' is not connected in Jules. "
                f"Please connect it at https://jules.google.com/settings\n"
                f"Available sources: {sources}"
            )
        return True

    # -------------------------------------------------------------------------
    # Sessions
    # -------------------------------------------------------------------------

    def create_session(
        self,
        prompt: str,
        github_repo: str,
        branch: str = "main",
        automation_mode: str = "AUTO_CREATE_PR",
        title: Optional[str] = None,
        require_plan_approval: bool = False,
    ) -> JulesSession:
        """Create a new Jules session (start a task).

        Args:
            prompt: Natural language task description
            github_repo: Repository in "owner/repo" format
            branch: Starting branch (default: "main")
            automation_mode: "AUTO_CREATE_PR" or omit for manual
            title: Optional human-readable session title
            require_plan_approval: If True, requires explicit plan approval

        Returns:
            JulesSession with session ID and initial status
        """
        payload: Dict[str, Any] = {
            "prompt": prompt,
            "sourceContext": {
                "source": f"sources/github/{github_repo}",
                "githubRepoContext": {
                    "startingBranch": branch,
                },
            },
        }

        if automation_mode:
            payload["automationMode"] = automation_mode

        if title:
            payload["title"] = title

        if require_plan_approval:
            payload["requirePlanApproval"] = True

        response = self._request("POST", "/sessions", json_data=payload)
        session = JulesSession.from_dict(response)

        logger.info(
            f"Created Jules session {session.id} for {github_repo}:{branch}"
        )
        return session

    def get_session(self, session_id: str) -> JulesSession:
        """Get session status.

        Args:
            session_id: Session ID

        Returns:
            JulesSession with current status
        """
        response = self._request("GET", f"/sessions/{session_id}")
        return JulesSession.from_dict(response)

    def list_sessions(
        self,
        page_size: int = 20,
        page_token: Optional[str] = None,
    ) -> tuple[List[JulesSession], Optional[str]]:
        """List sessions.

        Args:
            page_size: Number of sessions per page
            page_token: Token for pagination

        Returns:
            Tuple of (sessions list, next page token or None)
        """
        params: Dict[str, Any] = {"pageSize": page_size}
        if page_token:
            params["pageToken"] = page_token

        response = self._request("GET", "/sessions", params=params)
        sessions = [
            JulesSession.from_dict(s) for s in response.get("sessions", [])
        ]
        next_token = response.get("nextPageToken")
        return sessions, next_token

    # -------------------------------------------------------------------------
    # Activities
    # -------------------------------------------------------------------------

    def get_activities(
        self,
        session_id: str,
        page_size: int = 50,
        page_token: Optional[str] = None,
    ) -> tuple[List[JulesActivity], Optional[str]]:
        """Get session activities (events).

        Args:
            session_id: Session ID
            page_size: Number of activities per page
            page_token: Token for pagination

        Returns:
            Tuple of (activities list, next page token or None)
        """
        params: Dict[str, Any] = {"pageSize": page_size}
        if page_token:
            params["pageToken"] = page_token

        response = self._request(
            "GET", f"/sessions/{session_id}/activities", params=params
        )
        activities = [
            JulesActivity.from_dict(a) for a in response.get("activities", [])
        ]
        next_token = response.get("nextPageToken")
        return activities, next_token

    def get_all_activities(self, session_id: str) -> List[JulesActivity]:
        """Get all activities for a session (handles pagination).

        Args:
            session_id: Session ID

        Returns:
            Complete list of activities
        """
        all_activities: List[JulesActivity] = []
        page_token: Optional[str] = None

        while True:
            activities, page_token = self.get_activities(
                session_id, page_token=page_token
            )
            all_activities.extend(activities)
            if not page_token:
                break

        return all_activities

    # -------------------------------------------------------------------------
    # Session Actions
    # -------------------------------------------------------------------------

    def send_message(self, session_id: str, message: str) -> None:
        """Send a follow-up message to a session.

        Args:
            session_id: Session ID
            message: Message text
        """
        self._request(
            "POST",
            f"/sessions/{session_id}:sendMessage",
            json_data={"message": message},
        )
        logger.info(f"Sent message to Jules session {session_id}")

    def approve_plan(self, session_id: str) -> None:
        """Approve the session's plan.

        Only needed if requirePlanApproval was set to True when creating.

        Args:
            session_id: Session ID
        """
        self._request("POST", f"/sessions/{session_id}:approvePlan")
        logger.info(f"Approved plan for Jules session {session_id}")

    def cancel_session(self, session_id: str) -> bool:
        """Best-effort cancel a running Jules session.

        Jules API cancellation support is not guaranteed across versions.
        This method attempts to call the cancel endpoint and returns False
        if the endpoint is missing or the request fails.

        Args:
            session_id: Session ID

        Returns:
            True if the cancel request was accepted, False otherwise.
        """
        try:
            self._request("POST", f"/sessions/{session_id}:cancel")
            logger.info(f"Cancelled Jules session {session_id}")
            return True
        except JulesAPIError as e:
            # If the endpoint isn't supported, treat as best-effort failure.
            if e.status_code in (404, 405):
                logger.debug(
                    f"Cancel endpoint not supported for Jules session {session_id}"
                )
                return False
            logger.warning(f"Failed to cancel Jules session {session_id}: {e}")
            return False
        except Exception as e:
            logger.warning(f"Failed to cancel Jules session {session_id}: {e}")
            return False

    # -------------------------------------------------------------------------
    # Polling
    # -------------------------------------------------------------------------

    def poll_until_complete(
        self,
        session_id: str,
        poll_interval: int = 15,
        max_wait: int = 1800,
        on_activity: Optional[callable] = None,
    ) -> JulesSession:
        """Poll session until completion or timeout.

        Args:
            session_id: Session ID
            poll_interval: Seconds between polls (default: 15)
            max_wait: Maximum wait time in seconds (default: 1800 = 30 min)
            on_activity: Optional callback for new activities

        Returns:
            Final JulesSession

        Raises:
            JulesTimeoutError: If max_wait is exceeded
            JulesSessionError: If session fails
        """
        start_time = time.time()
        last_activity_count = 0

        while True:
            elapsed = time.time() - start_time
            if elapsed > max_wait:
                raise JulesTimeoutError(
                    f"Jules session {session_id} did not complete within "
                    f"{max_wait} seconds"
                )

            session = self.get_session(session_id)

            # Get new activities if callback provided
            if on_activity:
                activities = self.get_all_activities(session_id)
                for activity in activities[last_activity_count:]:
                    on_activity(activity)
                last_activity_count = len(activities)
                session.activities = activities

            if session.is_complete:
                if session.is_success:
                    logger.info(
                        f"Jules session {session_id} completed successfully"
                    )
                else:
                    logger.warning(
                        f"Jules session {session_id} ended with status: "
                        f"{session.status}, error: {session.error}"
                    )
                return session

            logger.debug(
                f"Jules session {session_id} status: {session.status}, "
                f"elapsed: {elapsed:.0f}s"
            )
            time.sleep(poll_interval)

    def stream_activities(
        self,
        session_id: str,
        poll_interval: int = 10,
        max_wait: int = 1800,
    ) -> Iterator[JulesActivity]:
        """Stream activities from a session until completion.

        This provides a streaming interface similar to CLI backends,
        yielding activities as they become available.

        Args:
            session_id: Session ID
            poll_interval: Seconds between polls
            max_wait: Maximum wait time in seconds

        Yields:
            JulesActivity objects as they become available
        """
        start_time = time.time()
        seen_activities: set[str] = set()

        while True:
            elapsed = time.time() - start_time
            if elapsed > max_wait:
                raise JulesTimeoutError(
                    f"Jules session {session_id} did not complete within "
                    f"{max_wait} seconds"
                )

            session = self.get_session(session_id)
            activities = self.get_all_activities(session_id)

            # Yield new activities
            for activity in activities:
                # Use timestamp + type as unique key
                activity_key = f"{activity.timestamp}:{activity.type}"
                if activity_key not in seen_activities:
                    seen_activities.add(activity_key)
                    yield activity

            if session.is_complete:
                return

            time.sleep(poll_interval)


# -----------------------------------------------------------------------------
# Helper Functions
# -----------------------------------------------------------------------------


def get_jules_api_key() -> Optional[str]:
    """Get Jules API key from unified store or environment.

    Priority:
      1. ~/.shinka/credentials.json (provider "jules")
      2. JULES_API_KEY environment variable

    Returns:
        API key string or None if not set
    """
    try:
        from shinka.tools.credentials import get_api_key as _get_api_key

        key = _get_api_key("jules")
        if key:
            return key
    except Exception:
        # Best-effort: fall back to environment below
        pass

    return os.environ.get("JULES_API_KEY")


def ensure_jules_api_key() -> str:
    """Get Jules API key or raise error.

    Returns:
        API key string

    Raises:
        JulesUnavailableError: If API key is not set
    """
    api_key = get_jules_api_key()
    if not api_key:
        raise JulesUnavailableError(
            "JULES_API_KEY environment variable is not set. "
            "Get your API key from https://jules.google.com/settings#api"
        )
    return api_key


def ensure_jules_available(github_repo: Optional[str] = None) -> str:
    """Verify Jules API key is set and optionally check repo is connected.

    Args:
        github_repo: Optional repo to verify is connected ("owner/repo")

    Returns:
        API key string

    Raises:
        JulesUnavailableError: If API key is not set
        JulesRepoNotConnectedError: If repo is specified but not connected
    """
    api_key = ensure_jules_api_key()

    if github_repo:
        client = JulesAPIClient(api_key)
        client.verify_repo_connected(github_repo)

    return api_key
