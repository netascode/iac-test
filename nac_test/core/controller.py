# SPDX-License-Identifier: MPL-2.0
# Copyright (c) 2025 Daniel Schmidt

"""Controller type detection utilities for NAC test framework.

This module provides utilities for detecting which network controller type (architecture)
is being targeted based on environment variables. Controller credentials are required for
ALL test types (both API and D2D tests) as they determine the architecture context.

The detection logic ensures exactly one controller type is configured at a time to prevent
ambiguous test execution contexts.

The module also provides a mapping from controller types to their defaults block prefixes,
enabling automatic defaults resolution without per-architecture configuration. For example,
when ACI_URL is detected, the framework automatically knows to look for defaults.apic in
the merged NAC data model.
"""

import logging
import os
import warnings
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType
from typing import cast

from nac_test._env import is_env_var_set
from nac_test.core.constants import ENV_CONTROLLER_CONTEXT
from nac_test.core.types import (
    AuthMethod,
    ControllerContext,
    ControllerTypeKey,
    CredentialKind,
)
from nac_test.exceptions import NacTestError

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class CredentialSet:
    """A single credential combination that can authenticate to a controller.

    Each set is self-contained: if ALL env_vars are present and non-empty,
    the controller is considered fully configured. When a controller has
    multiple CredentialSets, the first satisfied set wins (order matters).

    Attributes:
        fields: Mapping of semantic kind -> environment variable name,
            e.g. ``{"url": "ACI_URL", "username": "ACI_USERNAME",
            "password": "ACI_PASSWORD"}``. ``env_vars`` and ``kinds`` are
            derived from this single mapping, so they can never drift out of
            sync with each other. Lookups by kind (e.g. :func:`get_controller_url`,
            :func:`get_connection_params`) key off ``fields`` directly, so entry
            order within the mapping is not significant.
        label: Human-readable label for error messages (e.g., "API Token (20.18+)").
        auth_method: Identifier consumed by auth adapters in nac-test-pyats-common
            to select the authentication mechanism (e.g., "token", "session").
    """

    fields: Mapping[CredentialKind, str]
    label: str
    auth_method: AuthMethod = AuthMethod.SESSION

    def __post_init__(self) -> None:
        # Normalize to an immutable mapping so a frozen CredentialSet can't be
        # mutated in place via its `fields` dict.
        object.__setattr__(self, "fields", MappingProxyType(dict(self.fields)))

    @property
    def env_vars(self) -> tuple[str, ...]:
        """Environment variable names, in ``fields`` order."""
        return tuple(self.fields.values())

    @property
    def kinds(self) -> tuple[CredentialKind, ...]:
        """Semantic kind for each entry in ``env_vars``, in the same order."""
        return tuple(self.fields.keys())


@dataclass(frozen=True)
class ControllerConfig:
    """Configuration metadata for a supported controller type.

    Attributes:
        display_name: User-facing name (e.g., "APIC", "Catalyst Center").
        url_env_var: Environment variable name for the controller URL.
        env_var_prefix: Prefix for credential env vars (e.g., "ACI" → ACI_USERNAME).
        credential_sets: Ordered list of credential combinations. The first set
            whose env_vars are all present and non-empty wins. Every controller
            must have at least one CredentialSet.
        defaults_prefix: JMESPath prefix for the defaults block in NAC data models
            (e.g., "defaults.apic", "defaults.sdwan").
        insecure_env_var: Environment variable name for the SSL-verification-disable
            toggle (e.g., "ACI_INSECURE"). See :func:`should_verify_ssl`.
        cache_key: The controller_type string passed to AuthCache by the auth adapter.
            None for controllers that don't have an auth adapter in nac-test-pyats-common.
    """

    display_name: str
    url_env_var: str
    env_var_prefix: str
    credential_sets: tuple[CredentialSet, ...]
    defaults_prefix: str
    insecure_env_var: str
    cache_key: str | None = None


# Single source of truth for all controller configurations
# Replaces the registry from controller_auth.py
CONTROLLER_REGISTRY: dict[str, ControllerConfig] = {
    "ACI": ControllerConfig(
        display_name="APIC",
        url_env_var="ACI_URL",
        env_var_prefix="ACI",
        credential_sets=(
            CredentialSet(
                fields={
                    "url": "ACI_URL",
                    "username": "ACI_USERNAME",
                    "password": "ACI_PASSWORD",
                },
                label="Username/Password",
            ),
        ),
        defaults_prefix="defaults.apic",
        insecure_env_var="ACI_INSECURE",
        cache_key="ACI",
    ),
    "SDWAN": ControllerConfig(
        display_name="SDWAN Manager",
        url_env_var="SDWAN_URL",
        env_var_prefix="SDWAN",
        credential_sets=(
            CredentialSet(
                fields={
                    "url": "SDWAN_URL",
                    "token": "SDWAN_API_TOKEN",
                },
                label="API Token (20.18+)",
                auth_method=AuthMethod.TOKEN,
            ),
            CredentialSet(
                fields={
                    "url": "SDWAN_URL",
                    "username": "SDWAN_USERNAME",
                    "password": "SDWAN_PASSWORD",
                },
                label="Username/Password",
            ),
        ),
        defaults_prefix="defaults.sdwan",
        insecure_env_var="SDWAN_INSECURE",
        cache_key="SDWAN_MANAGER",
    ),
    "CC": ControllerConfig(
        display_name="Catalyst Center",
        url_env_var="CC_URL",
        env_var_prefix="CC",
        credential_sets=(
            CredentialSet(
                fields={
                    "url": "CC_URL",
                    "username": "CC_USERNAME",
                    "password": "CC_PASSWORD",
                },
                label="Username/Password",
            ),
        ),
        defaults_prefix="defaults.catc",
        insecure_env_var="CC_INSECURE",
        cache_key="CC",
    ),
    "MERAKI": ControllerConfig(
        display_name="Meraki",
        url_env_var="MERAKI_URL",
        env_var_prefix="MERAKI",
        credential_sets=(
            CredentialSet(
                fields={
                    "url": "MERAKI_URL",
                    "username": "MERAKI_USERNAME",
                    "password": "MERAKI_PASSWORD",
                },
                label="Username/Password",
            ),
        ),
        defaults_prefix="defaults.meraki",
        insecure_env_var="MERAKI_INSECURE",
    ),
    "FMC": ControllerConfig(
        display_name="Firepower Management Center",
        url_env_var="FMC_URL",
        env_var_prefix="FMC",
        credential_sets=(
            CredentialSet(
                fields={
                    "url": "FMC_URL",
                    "username": "FMC_USERNAME",
                    "password": "FMC_PASSWORD",
                },
                label="Username/Password",
            ),
        ),
        defaults_prefix="defaults.fmc",
        insecure_env_var="FMC_INSECURE",
    ),
    "ISE": ControllerConfig(
        display_name="ISE",
        url_env_var="ISE_URL",
        env_var_prefix="ISE",
        credential_sets=(
            CredentialSet(
                fields={
                    "url": "ISE_URL",
                    "username": "ISE_USERNAME",
                    "password": "ISE_PASSWORD",
                },
                label="Username/Password",
            ),
        ),
        defaults_prefix="defaults.ise",
        insecure_env_var="ISE_INSECURE",
    ),
    "IOSXE": ControllerConfig(
        display_name="IOS XE",
        url_env_var="IOSXE_URL",
        env_var_prefix="IOSXE",
        # Direct device access, no controller credentials required.
        # IOSXE_URL and IOSXE_HOST serve the same purpose (IOSXE_URL is
        # being phased out) - both map to kind="url".
        credential_sets=(
            CredentialSet(
                fields={
                    "url": "IOSXE_URL",
                    "username": "IOSXE_USERNAME",
                    "password": "IOSXE_PASSWORD",
                },
                label="Device URL",
            ),
            CredentialSet(
                fields={
                    "url": "IOSXE_HOST",
                    "username": "IOSXE_USERNAME",
                    "password": "IOSXE_PASSWORD",
                },
                label="Device Host",
            ),
        ),
        defaults_prefix="defaults.iosxe",
        insecure_env_var="IOSXE_INSECURE",
    ),
    "NXOS": ControllerConfig(
        display_name="NX-OS",
        url_env_var="NXOS_HOST",
        env_var_prefix="NXOS",
        # Direct device access: there is no controller (APIC/Manager) to
        # authenticate against first, but device SSH credentials are still
        # required for detection and for default credential resolution.
        credential_sets=(
            CredentialSet(
                fields={
                    "url": "NXOS_HOST",
                    "username": "NXOS_USERNAME",
                    "password": "NXOS_PASSWORD",
                },
                label="Device Host",
            ),
        ),
        defaults_prefix="defaults.nxos",
        insecure_env_var="NXOS_INSECURE",
    ),
}

# Module-level cache for the credential set that was matched during detection.
# Populated by detect_controller_type(), consumed by get_matched_credential_set().
_matched_credential_sets: dict[str, CredentialSet] = {}


class ResolutionError(NacTestError):
    """Base for controller resolution failures."""


class NoCredentialsFound(ResolutionError):
    """No controller env vars detected at all."""


class MultipleControllersFound(ResolutionError):
    """Multiple controller types have complete credentials configured."""

    def __init__(self, controllers: list[str]):
        self.controllers = controllers
        super().__init__(
            f"Multiple controller credentials detected: {', '.join(controllers)}"
        )


class IncompleteCredentials(ResolutionError):
    """Some controller env vars present but no complete credential set."""

    def __init__(self, partial_controllers: list[str] | list[ControllerTypeKey]):
        self.partial_controllers = partial_controllers
        super().__init__(
            f"Incomplete credentials for: {', '.join(partial_controllers)}"
        )


def resolve_controller() -> ControllerContext:
    """Resolve the active controller from environment variables.

    Single source of truth for controller detection.  Returns a
    :class:`ControllerContext` on success; raises a typed
    :class:`ResolutionError` subclass on failure.  The caller decides
    how to handle failures — this function never calls ``sys.exit()``.

    Side-effects:
        * Populates ``_matched_credential_sets`` (same as the legacy
          ``detect_controller_type()``).

    Returns:
        ControllerContext with ``controller_type`` and ``auth_method``.

    Raises:
        NoCredentialsFound: No controller env vars detected at all.
        MultipleControllersFound: More than one controller fully configured.
        IncompleteCredentials: Some env vars present but no complete set.
    """
    logger.debug("Resolving controller from environment")
    complete, partial = _find_credential_sets()

    if len(complete) > 1:
        raise MultipleControllersFound(list(complete.keys()))

    if not complete and not partial:
        raise NoCredentialsFound("No controller credentials found in environment.")

    if not complete and partial:
        raise IncompleteCredentials(partial)

    # Exactly one complete set — success
    controller_type = next(iter(complete))
    matched_cred_set = complete[controller_type]
    _matched_credential_sets[controller_type] = matched_cred_set

    ctx = ControllerContext(
        controller_type=controller_type,
        auth_method=matched_cred_set.auth_method,
    )

    logger.info(
        "Resolved controller: %s (auth_method=%s)",
        controller_type,
        matched_cred_set.auth_method,
    )
    return ctx


def get_controller_context() -> ControllerContext:
    """Get the resolved controller context in a subprocess.

    This function is designed for use by ``NACTestBase.setup()`` in PyATS
    subprocesses. The parent process (``CombinedOrchestrator``) resolves the
    controller via ``resolve_controller()`` and passes the result to
    ``PyATSOrchestrator``, which serializes it to ``NAC_TEST_CONTROLLER_CONTEXT``
    before launching subprocesses.

    **Primary path (subprocess):** Deserializes from ``NAC_TEST_CONTROLLER_CONTEXT``
    environment variable set by ``PyATSOrchestrator``.

    **Fallback (transitional):** If the env var is absent, falls back to
    ``detect_controller_type()`` for backwards compatibility. This fallback
    will be removed in Phase 3 once all consumers are migrated.

    Returns:
        ControllerContext with controller_type and auth_method.

    Raises:
        ValueError: If no controller credentials are found (via fallback path).
    """
    raw = os.environ.get(ENV_CONTROLLER_CONTEXT)
    if raw:
        return ControllerContext.from_json(raw)

    # --- Transitional fallback (remove in Phase 3) -----------------------
    logging.getLogger(__name__).info(
        "NAC_TEST_CONTROLLER_CONTEXT not set — falling back to "
        "detect_controller_type(). This fallback will be removed in a "
        "future release."
    )

    controller_type = detect_controller_type()
    return ControllerContext(
        controller_type=controller_type,
        auth_method=_infer_auth_method(controller_type),
    )


def format_resolution_error(error: ResolutionError) -> str:
    """Format a :class:`ResolutionError` into a user-facing message.

    Re-uses the existing detailed error formatters so that CLI output
    stays identical to the legacy ``detect_controller_type()`` path.
    """
    if isinstance(error, MultipleControllersFound):
        return _format_multiple_credentials_error(error.controllers)
    if isinstance(error, IncompleteCredentials):
        return _format_incomplete_credentials_error(error.partial_controllers)
    if isinstance(error, NoCredentialsFound):
        return _format_no_credentials_error()
    return str(error)


def get_display_name(controller_type: str) -> str:
    """Get the user-facing display name for a controller type.

    Looks up the display name from CONTROLLER_REGISTRY. If the controller type
    is not registered, returns the controller_type string as-is for graceful
    degradation.

    Args:
        controller_type: The internal controller type key (e.g., "ACI", "SDWAN", "CC").

    Returns:
        The user-facing display name (e.g., "APIC", "SDWAN Manager", "Catalyst Center"),
        or the controller_type string if not found in registry.
    """
    config = CONTROLLER_REGISTRY.get(controller_type)
    return config.display_name if config else controller_type


def get_env_var_prefix(controller_type: str) -> str:
    """Get the environment variable prefix for a controller type.

    Looks up the env_var_prefix from CONTROLLER_REGISTRY. If the controller type
    is not registered, returns the controller_type string as-is for graceful
    degradation.

    Args:
        controller_type: The internal controller type key (e.g., "ACI", "SDWAN", "CC").

    Returns:
        The environment variable prefix (e.g., "ACI", "SDWAN", "CC"),
        or the controller_type string if not found in registry.
    """
    config = CONTROLLER_REGISTRY.get(controller_type)
    return config.env_var_prefix if config else controller_type


def get_defaults_prefix(controller_type: str) -> str:
    """Get the JMESPath defaults prefix for a controller type.

    Looks up the defaults_prefix from CONTROLLER_REGISTRY. If the controller type
    is not registered, constructs a default prefix of "defaults.<controller_type_lower>"
    for graceful degradation.

    Args:
        controller_type: The internal controller type key (e.g., "ACI", "SDWAN", "CC").

    Returns:
        The JMESPath defaults prefix (e.g., "defaults.apic", "defaults.sdwan"),
        or "defaults.<controller_type_lower>" if not found in registry.

    Example:
        >>> get_defaults_prefix("ACI")
        'defaults.apic'
        >>> get_defaults_prefix("SDWAN")
        'defaults.sdwan'
        >>> get_defaults_prefix("UNKNOWN")
        'defaults.unknown'
    """
    config = CONTROLLER_REGISTRY.get(controller_type)
    return config.defaults_prefix if config else f"defaults.{controller_type.lower()}"


def get_controller_url(controller_type: str) -> str:
    """Get the controller URL from environment variables.

    Iterates through credential sets in order, returning the first env var value
    found. This follows the same first-match-wins pattern as _find_credential_sets.

    Args:
        controller_type: The internal controller type key (e.g., "ACI", "SDWAN", "IOSXE").

    Returns:
        The controller URL value from the environment.

    Raises:
        KeyError: If no credential set env var has a URL value set.

    Example:
        >>> os.environ["ACI_URL"] = "https://apic.example.com"
        >>> get_controller_url("ACI")
        'https://apic.example.com'

        >>> os.environ["IOSXE_HOST"] = "192.168.1.1"
        >>> get_controller_url("IOSXE")  # Returns IOSXE_HOST when IOSXE_URL not set
        '192.168.1.1'
    """
    config = CONTROLLER_REGISTRY.get(controller_type)

    if config is None:
        # Fallback for unknown controller types
        return os.environ[f"{controller_type}_URL"]

    # Primary URL from the explicit url_env_var field
    value = os.environ.get(config.url_env_var, "").strip()
    if value:
        return value

    # Fallback for alternative URL vars (e.g., IOSXE_HOST)
    for cred_set in config.credential_sets:
        url_var = cred_set.fields.get("url")
        if url_var and url_var != config.url_env_var:
            alt = os.environ.get(url_var, "").strip()
            if alt:
                return alt

    raise KeyError(config.url_env_var)


def get_credential_vars(
    controller_type: str,
) -> list[tuple[CredentialKind, str]]:
    """Return deduplicated (kind, env_var) pairs for non-URL credential fields.

    Iterates all credential sets for the given controller type and collects
    unique credential fields, excluding URL (which is accessed separately
    via :func:`get_controller_url`).

    This is the single-source primitive used by CLI banners and HTML report
    templates to generate remediation instructions.

    Args:
        controller_type: The internal controller type key (e.g., "ACI", "SDWAN").

    Returns:
        List of (kind, env_var_name) tuples, deduplicated by var name.
        E.g. ``[("username", "ACI_USERNAME"), ("password", "ACI_PASSWORD")]``
        or ``[("token", "SDWAN_API_TOKEN"), ("username", "SDWAN_USERNAME"), ...]``

    Raises:
        KeyError: If controller_type is not registered.
    """
    config = CONTROLLER_REGISTRY[controller_type]
    seen: set[str] = set()
    result: list[tuple[CredentialKind, str]] = []
    for cred_set in config.credential_sets:
        for kind, var in cred_set.fields.items():
            if kind == "url" or var in seen:
                continue
            seen.add(var)
            result.append((kind, var))
    return result


_INSECURE_TRUE_VALUES = ("true", "1", "yes")


def should_verify_ssl(controller_type: str, default: bool = False) -> bool:
    """Determine whether SSL certificate verification should be enabled.

    Single source of truth for reading a controller's ``{PREFIX}_INSECURE``
    environment variable (see :attr:`ControllerConfig.insecure_env_var`),
    replacing per-adapter ``os.environ.get(f"{prefix}_INSECURE", ...)`` calls
    in nac-test-pyats-common.

    Args:
        controller_type: The internal controller type key (e.g., "ACI", "SDWAN", "CC").
        default: Value to use when the env var is unset or empty. Defaults to
            False (skip verification), matching existing adapter behavior to
            keep lab/self-signed deployments working without extra config.

    Returns:
        True if SSL verification should be performed.

    Raises:
        KeyError: If controller_type is not registered.

    Example:
        >>> os.environ["ACI_INSECURE"] = "true"
        >>> should_verify_ssl("ACI")
        False
        >>> os.environ["ACI_INSECURE"] = "false"
        >>> should_verify_ssl("ACI")
        True
    """
    config = CONTROLLER_REGISTRY[controller_type]
    raw = os.environ.get(config.insecure_env_var, "").strip()
    if not raw:
        return default
    # Env var is "{PREFIX}_INSECURE": true/1/yes means insecure (don't verify)
    return raw.lower() not in _INSECURE_TRUE_VALUES


def get_connection_params(
    controller_type: str, auth_method: AuthMethod
) -> dict[CredentialKind, str]:
    """Resolve connection values by kind for a controller_type/auth_method.

    Single source of truth for reading connection-related env var values.
    Looks up the ``CredentialSet``(s) in ``CONTROLLER_REGISTRY[controller_type]``
    whose ``auth_method`` matches, then reads each of its ``env_vars`` from
    ``os.environ``, keyed by the corresponding entry in ``kinds`` (e.g.
    ``"url"``, ``"username"``, ``"password"``, ``"token"``).

    When multiple credential sets share the same ``auth_method`` (e.g. IOS-XE's
    URL and Host variants, both ``auth_method="session"``), the first one that
    is fully satisfied (all env vars present) is used, so it doesn't matter
    which of the equivalent variables the caller actually configured. If none
    are fully satisfied, the first matching set is used to build the
    "missing variable(s)" error, matching prior behavior.

    Args:
        controller_type: The internal controller type key (e.g., "ACI", "SDWAN", "CC").
        auth_method: The auth method to match against the controller's
            credential sets (e.g., "session", "token").

    Returns:
        Dict keyed by kind, e.g. ``{"url": ..., "username": ..., "password": ...}``
        or ``{"url": ..., "token": ...}``.

    Raises:
        KeyError: If controller_type is not registered.
        ValueError: If no credential set matches auth_method, or if any of its
            env vars are missing/empty (message lists the missing var names).

    Example:
        >>> os.environ.update({"SDWAN_URL": "https://vmanage.example.com",
        ...                     "SDWAN_API_TOKEN": "abc.def.ghi"})
        >>> get_connection_params("SDWAN", "token")
        {'url': 'https://vmanage.example.com', 'token': 'abc.def.ghi'}
    """
    config = CONTROLLER_REGISTRY[controller_type]

    candidates = [cs for cs in config.credential_sets if cs.auth_method == auth_method]
    if not candidates:
        raise ValueError(
            f"No credential set for controller_type={controller_type!r} matches "
            f"auth_method={auth_method!r}."
        )

    # Prefer a fully-satisfied candidate so equivalent variables (e.g. IOS-XE's
    # IOSXE_URL vs IOSXE_HOST, both auth_method="session") don't collide -
    # whichever one the caller actually configured wins. If none is fully
    # satisfied, report missing vars for whichever candidate the caller has
    # actually started configuring (most env vars already set), so a user who
    # set IOSXE_HOST but forgot the password isn't told to set IOSXE_URL
    # instead. Ties (including "nothing configured at all") keep the first
    # candidate, matching prior behavior.
    def _set_var_count(cs: CredentialSet) -> int:
        return sum(1 for var in cs.env_vars if os.environ.get(var, "").strip())

    cred_set = next(
        (
            cs
            for cs in candidates
            if all(os.environ.get(var, "").strip() for var in cs.env_vars)
        ),
        None,
    )
    if cred_set is None:
        cred_set = max(candidates, key=_set_var_count)

    values: dict[CredentialKind, str] = {}
    missing: list[str] = []
    for kind, var in cred_set.fields.items():
        value = os.environ.get(var, "").strip()
        if value:
            values[kind] = value
        else:
            missing.append(var)

    if missing:
        raise ValueError(
            f"Missing required environment variable(s) for {controller_type}: "
            f"{', '.join(missing)}"
        )

    return values


def detect_controller_type() -> ControllerTypeKey:
    """Detect the controller type based on environment variables.

    .. deprecated::
        This function is retained for backwards compatibility with external
        packages (e.g., ``nac-test-pyats-common``) that have not yet migrated
        to :func:`resolve_controller`. New code should use ``resolve_controller()``
        directly and handle :class:`ResolutionError` subtypes. This function
        will be removed once all consumers have migrated.

    This function examines environment variables to determine which network controller
    architecture is being targeted. It ensures exactly one controller type has credentials
    configured to prevent ambiguous test contexts.

    Controller credentials are required for ALL test types:
    - API tests: Use credentials directly for controller authentication
    - D2D tests: Use controller type to determine device resolution logic

    Returns:
        The detected controller type (e.g., "ACI", "SDWAN", "CC", "MERAKI", "FMC", "ISE").

    Raises:
        ValueError: If no controller credentials are found, multiple controllers are
            configured, or credentials are incomplete.

    Example:
        >>> os.environ.update({"ACI_URL": "https://apic.local",
        ...                    "ACI_USERNAME": "admin",
        ...                    "ACI_PASSWORD": "pass"})
        >>> controller = detect_controller_type()
        >>> print(controller)
        "ACI"

    Note:
        This function delegates to :func:`resolve_controller` and converts typed
        exceptions to ``ValueError`` for backwards compatibility with existing callers.
    """
    warnings.warn(
        "detect_controller_type() is deprecated; use resolve_controller() instead.",
        DeprecationWarning,
        stacklevel=2,
    )
    try:
        ctx = resolve_controller()
        return ctx.controller_type
    except ResolutionError as e:
        raise ValueError(format_resolution_error(e)) from e


def get_matched_credential_set(controller_type: str) -> CredentialSet | None:
    """Get the credential set that was matched during controller detection.

    .. deprecated::
        This function is a transitional API for ``nac-test-pyats-common`` auth
        adapters. It will be removed in Phase 3 once auth adapters migrate to
        using ``get_controller_context().auth_method`` directly. New code should
        not use this function.

    Returns the CredentialSet that satisfied detection for the given controller
    type. This is populated by detect_controller_type() / resolve_controller()
    and is intended for use by auth adapters in nac-test-pyats-common to
    determine which authentication mechanism to use.

    Args:
        controller_type: The controller type key (e.g., "SDWAN", "ACI").

    Returns:
        The matched CredentialSet, or None if detection has not been called
        or the controller type was not detected.
    """
    warnings.warn(
        "get_matched_credential_set() is deprecated; use "
        "get_controller_context().auth_method instead.",
        DeprecationWarning,
        stacklevel=2,
    )
    return _matched_credential_sets.get(controller_type)


def _find_credential_sets() -> tuple[
    dict[ControllerTypeKey, CredentialSet],
    list[ControllerTypeKey],
]:
    """Find complete and partial credential sets in environment.

    For each controller, iterates through its credential_sets in order. The first
    set whose env_vars are all present and non-empty marks the controller as
    complete. If no set is fully satisfied but at least one variable from any set
    is present, the controller is reported as partial.

    Returns:
        A tuple containing:
            - Dictionary mapping complete controller types to the winning CredentialSet
            - List of controller types with partial credentials
    """
    complete: dict[ControllerTypeKey, CredentialSet] = {}
    partial: list[ControllerTypeKey] = []

    for controller_type, config in CONTROLLER_REGISTRY.items():
        found_complete = False
        has_any_var = False
        ct_key = cast(ControllerTypeKey, controller_type)

        for cred_set in config.credential_sets:
            all_present = True

            for var in cred_set.env_vars:
                if is_env_var_set(var):
                    has_any_var = True
                    logger.debug(f"  {controller_type}: Found {var}")
                else:
                    all_present = False

            if all_present:
                complete[ct_key] = cred_set
                logger.debug(f"  {controller_type}: Complete via {cred_set.label}")
                found_complete = True
                break

        if not found_complete and has_any_var:
            partial.append(ct_key)

    return complete, partial


def _infer_auth_method(controller_type: str) -> AuthMethod:
    """Infer auth_method by scanning env vars for a controller type.

    Used only in the transitional fallback path of
    ``get_controller_context()`` when ``NAC_TEST_CONTROLLER_CONTEXT``
    is absent.  Mirrors the logic of ``_find_credential_sets()`` but
    returns only the auth_method string.
    """
    config = CONTROLLER_REGISTRY.get(controller_type)
    if config is None:
        return AuthMethod.SESSION
    for cred_set in config.credential_sets:
        if all(is_env_var_set(v) for v in cred_set.env_vars):
            return cred_set.auth_method
    return AuthMethod.SESSION


def _format_incomplete_credentials_error(partial_controllers: Sequence[str]) -> str:
    """Format error message for incomplete controller credentials.

    Creates a detailed error message listing each partially configured
    controller and its accepted credential sets, so the user knows
    exactly which variables are needed.

    Args:
        partial_controllers: List of controller types with partial credentials.

    Returns:
        Formatted error message with accepted credential sets.

    Example:
        >>> error = _format_incomplete_credentials_error(["SDWAN"])
        >>> print(error)
        Incomplete controller credentials detected:
        ...
    """
    lines_parts: list[str] = []
    for controller in partial_controllers:
        config = CONTROLLER_REGISTRY[controller]
        set_descriptions = [
            f"{cs.label}: {' + '.join(cs.env_vars)}" for cs in config.credential_sets
        ]
        line = f"{controller}: incomplete credentials"
        line += "\n    Accepted credential sets:\n"
        line += "\n".join(f"      - {desc}" for desc in set_descriptions)
        lines_parts.append(line)
    lines = "\n".join(f"  - {info}" for info in lines_parts)
    return (
        f"Incomplete controller credentials detected:\n"
        f"{lines}\n\n"
        f"Please provide all required variables for one of the "
        f"accepted credential sets listed above."
    )


def _format_multiple_credentials_error(controllers: list[str]) -> str:
    """Format error message for multiple controller credentials.

    Creates a detailed error message with remediation options when multiple
    controller types have complete credentials configured.

    Args:
        controllers: List of controller types with complete credentials.

    Returns:
        Formatted error message with remediation steps.

    Example:
        >>> error = _format_multiple_credentials_error(["ACI", "SDWAN"])
        >>> print(error)
        Multiple controller credentials detected: ACI, SDWAN
        ...
    """
    controller_list = ", ".join(controllers)

    message = (
        f"Multiple controller credentials detected: {controller_list}\n\n"
        f"The test framework requires exactly one controller type to be configured.\n\n"
        f"Remediation options:\n"
        f"1. Keep only one controller's credentials and unset the others:\n"
    )

    # Collect all env vars per controller (union of all credential sets)
    def _all_env_vars(controller: str) -> list[str]:
        config = CONTROLLER_REGISTRY[controller]
        seen: set[str] = set()
        result: list[str] = []
        for cs in config.credential_sets:
            for v in cs.env_vars:
                if v not in seen:
                    seen.add(v)
                    result.append(v)
        return result

    # Add specific unset commands for each controller
    for controller in controllers:
        other_controllers = [c for c in controllers if c != controller]
        vars_to_remove = []
        for other in other_controllers:
            vars_to_remove.extend(_all_env_vars(other))

        unset_command = f"   unset {' '.join(vars_to_remove)}"
        message += f"\n   To use {controller} only:\n{unset_command}\n"

    message += (
        "\n2. Use a separate shell session for each controller type\n"
        "\n3. Use environment variable management tools (direnv, dotenv) to switch contexts"
    )

    return message


def _format_no_credentials_error() -> str:
    """Format error message when no controller credentials are found.

    Creates a detailed error message with setup instructions when no controller
    credentials are detected in the environment.

    Returns:
        Formatted error message with setup guidance.

    Example:
        >>> error = _format_no_credentials_error()
        >>> print(error)
        No controller credentials found in environment.
        ...
    """
    message = (
        "No controller credentials found in environment.\n\n"
        "Controller credentials are required for ALL test types (API and D2D).\n"
        "The framework uses these to determine the architecture context.\n\n"
        "Please set environment variables for ONE of the following controller types:\n\n"
    )

    for controller_type, config in CONTROLLER_REGISTRY.items():
        message += f"{controller_type}:\n"
        for i, cred_set in enumerate(config.credential_sets):
            if i > 0:
                message += "  Or\n"
            if len(config.credential_sets) > 1:
                message += f"  ({cred_set.label}):\n"
            for var in cred_set.env_vars:
                message += f"  export {var}=<value>\n"
        message += "\n"

    message += (
        "Example for ACI:\n"
        "  export ACI_URL=https://apic.example.com\n"
        "  export ACI_USERNAME=admin\n"
        "  export ACI_PASSWORD=yourpassword\n\n"
        "Note: Set credentials for only ONE controller type at a time."
    )

    return message
