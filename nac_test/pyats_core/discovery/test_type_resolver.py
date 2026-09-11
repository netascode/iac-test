# SPDX-License-Identifier: MPL-2.0
# Copyright (c) 2025 Daniel Schmidt

"""Test metadata resolver for NAC-Test framework.

This module provides automated metadata extraction for PyATS test files,
including:

1. **Test Type Detection**: Determining whether tests should be classified as
   API (controller/REST) or D2D (Direct-to-Device/SSH) tests based on base
   class inheritance.

2. **Groups Extraction**: Extracting the `groups` class attribute from test
   classes for tag-based filtering using Robot Framework tag pattern semantics.

The TestMetadataResolver uses static AST analysis to extract metadata without
importing or executing any test code, ensuring fast and safe analysis.

Detection Strategy (Priority Order):
    1. **AST Analysis** (Highest Priority):
       - Statically analyzes Python AST to detect base class inheritance
       - Maps known base classes to test types via BASE_CLASS_MAPPING
       - Handles both direct (Name) and qualified (Attribute) class references
       - Extracts `groups` attribute for tag-based filtering
       - Most reliable method with <5ms performance per file

    2. **Directory Structure** (Fallback):
       - Checks if test file is under a `/d2d/` directory path
       - Simple path-based heuristic for organized codebases
       - Instant detection with minimal overhead

    3. **Default Classification** (Last Resort):
       - Falls back to 'api' type when no other indicators found
       - Ensures all tests have a valid classification

Performance Characteristics:
    - AST parsing: <5ms per file (typical)
    - Directory check: <0.1ms per file

Extending for New Architectures:
    To add support for new network architectures or test bases:

    1. Add the base class mapping to BASE_CLASS_MAPPING:
       ```python
       BASE_CLASS_MAPPING["MyNewTestBase"] = "api"  # or "d2d"
       ```

    2. If creating a new test type beyond api/d2d:
       - Add to VALID_TEST_TYPES set
       - Update discovery logic in test_discovery.py
       - Ensure archive separation logic handles new type

Example Usage:
    ```python
    from nac_test.pyats_core.discovery.test_type_resolver import TestMetadataResolver
    from pathlib import Path

    # Initialize resolver
    resolver = TestMetadataResolver()

    # Get full metadata (type + groups) for tag filtering
    metadata = resolver.resolve(Path("/path/to/verify_bgp.py"))
    # Returns: TestFileMetadata(path=..., test_type="api", groups=["health", "bgp"])

    # Use metadata for filtering and categorization
    if metadata.test_type == "d2d":
        # Handle D2D test (SSH-based)
        pass
    else:
        # Handle API test (REST-based)
        pass
    ```

Module Constants:
    BASE_CLASS_MAPPING: Maps base class names to test types
    VALID_TEST_TYPES: Set of valid test type values
    DEFAULT_TEST_TYPE: Fallback test type when detection fails

Exceptions:
    NoRecognizedBaseError: Raised when AST analysis finds no recognized base class
"""

import ast
import logging
from pathlib import Path
from typing import Final

from nac_test.pyats_core.common.types import (
    DEFAULT_TEST_TYPE,
    TestFileMetadata,
    TestType,
)

logger = logging.getLogger(__name__)

# Base class to test type mapping
# This dictionary maps known PyATS test base class names to their test types
BASE_CLASS_MAPPING: Final[dict[str, TestType]] = {
    # API test bases (controller/REST tests)
    "NACTestBase": "api",  # Generic base, defaults to API
    "APICTestBase": "api",  # ACI/APIC controller tests
    "SDWANManagerTestBase": "api",  # SD-WAN vManage/Manager controller tests
    "CatalystCenterTestBase": "api",  # Catalyst Center (formerly DNAC) tests
    "MerakiTestBase": "api",  # Meraki Dashboard API tests
    "FMCTestBase": "api",  # Firepower Management Center tests
    "ISETestBase": "api",  # Identity Services Engine tests
    # D2D test bases (SSH/device tests)
    "SSHTestBase": "d2d",  # Generic SSH-based device tests
    "SDWANTestBase": "d2d",  # SD-WAN edge device tests (cEdge/vEdge)
    "IOSXETestBase": "d2d",  # IOS-XE device tests
    "NXOSTestBase": "d2d",  # NX-OS device tests
    "FTDTestBase": "d2d",  # Firepower Threat Defense (FTD) CLISH device tests
    "IOSTestBase": "d2d",  # Classic IOS device tests
}


class NoRecognizedBaseError(Exception):
    """Exception raised when no recognized base class is found during AST analysis.

    This exception is raised when the AST parser successfully analyzes a test file
    but cannot find any base classes that match the known mappings in BASE_CLASS_MAPPING.
    This is a normal condition that triggers fallback to directory-based detection.

    Attributes:
        filename: Path to the test file that was analyzed
        found_bases: List of base class names that were found but not recognized
    """

    def __init__(self, filename: str, found_bases: list[str] | None = None) -> None:
        """Initialize the exception with file context.

        Args:
            filename: Path to the test file that was analyzed
            found_bases: Optional list of base class names that were found but not recognized
        """
        self.filename = filename
        self.found_bases = found_bases or []

        if found_bases:
            message = (
                f"No recognized base class found in {filename}. "
                f"Found bases: {', '.join(found_bases)}, "
                f"but none match known mappings."
            )
        else:
            message = f"No base classes found in {filename}"

        super().__init__(message)


class TestMetadataResolver:
    """Resolves test metadata (type and groups) using static AST analysis.

    This class extracts metadata from PyATS test files without importing or
    executing the code:

    1. **Test Type**: Classifies tests as API (controller/REST) or D2D
       (Direct-to-Device/SSH) based on base class inheritance.

    2. **Groups**: Extracts the `groups` class attribute for tag-based
       filtering using Robot Framework tag pattern semantics.

    The resolver uses a multi-tier detection strategy with AST analysis as
    the primary method and directory-based fallback.

    """

    @staticmethod
    def _extract_metadata(file_path: Path) -> TestFileMetadata:
        """Extract test type and groups by statically analyzing base class inheritance.

        This method parses the Python file into an Abstract Syntax Tree (AST)
        and examines the base classes of all top-level class definitions.
        It maps recognized base class names to their corresponding test types
        and extracts the `groups` class attribute for tag-based filtering.

        Args:
            file_path: Path to the Python test file to analyze

        Returns:
            TestFileMetadata with path, test_type, and groups

        Raises:
            NoRecognizedBaseError: When no recognized base class is found
            OSError: When the file cannot be read (propagated)
            SyntaxError: When the Python file has syntax errors (propagated)
        """
        logger.debug(f"Analyzing AST for file: {file_path}")

        content = file_path.read_text(encoding="utf-8")
        tree = ast.parse(content, filename=str(file_path))

        found_bases: list[str] = []
        detected_test_type: TestType | None = None
        detected_groups: list[str] = []

        for node in tree.body:
            if not isinstance(node, ast.ClassDef):
                continue

            logger.debug(f"Found class: {node.name}")

            for base in node.bases:
                base_name: str | None = None

                if isinstance(base, ast.Name):
                    # Direct inheritance: class MyTest(SSHTestBase)
                    base_name = base.id
                    logger.debug(f"  Direct base: {base_name}")
                elif isinstance(base, ast.Attribute):
                    # Qualified inheritance: class MyTest(module.SSHTestBase)
                    # We only care about the class name, not the module
                    base_name = base.attr
                    logger.debug(f"  Qualified base: {base_name}")

                if base_name:
                    found_bases.append(base_name)

                    if base_name in BASE_CLASS_MAPPING and detected_test_type is None:
                        detected_test_type = BASE_CLASS_MAPPING[base_name]
                        logger.info(
                            f"Detected test type '{detected_test_type}' from base class "
                            f"'{base_name}' in {file_path}"
                        )

            if detected_test_type is not None:
                groups = TestMetadataResolver._extract_groups_from_class(node)
                if groups:
                    detected_groups = groups
                    logger.debug(f"  Extracted groups: {groups}")
                break

        if detected_test_type is not None:
            return TestFileMetadata(
                path=file_path, test_type=detected_test_type, groups=detected_groups
            )

        logger.debug(
            f"No recognized base class in {file_path}. "
            f"Found bases: {found_bases if found_bases else 'none'}"
        )
        raise NoRecognizedBaseError(str(file_path), found_bases)

    @staticmethod
    def _extract_groups_from_class(class_node: ast.ClassDef) -> list[str]:
        """Extract the `groups` attribute from a class definition.

        Looks for class-level assignments like:
            groups = ['health', 'bgp', 'ospf']

        Args:
            class_node: AST ClassDef node to examine

        Returns:
            List of group strings, empty if no groups attribute found
        """
        for item in class_node.body:
            # Look for simple assignments: groups = [...]
            if isinstance(item, ast.Assign):
                for target in item.targets:
                    if isinstance(target, ast.Name) and target.id == "groups":
                        return TestMetadataResolver._parse_list_value(item.value)

            # Also handle annotated assignments: groups: list[str] = [...]
            if isinstance(item, ast.AnnAssign):
                if (
                    isinstance(item.target, ast.Name)
                    and item.target.id == "groups"
                    and item.value is not None
                ):
                    return TestMetadataResolver._parse_list_value(item.value)

        return []

    @staticmethod
    def _parse_list_value(node: ast.expr) -> list[str]:
        """Parse a list literal from an AST node.

        Args:
            node: AST expression node (expected to be ast.List)

        Returns:
            List of string values, empty if not a valid string list
        """
        if not isinstance(node, ast.List):
            return []

        result: list[str] = []
        for elt in node.elts:
            if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
                result.append(elt.value)
        return result

    @staticmethod
    def resolve(test_file: Path) -> TestFileMetadata:
        """Resolve test metadata (type and groups) for a test file.

        This is the main API entry point for test metadata extraction. It uses
        a three-tier detection strategy to determine test type and extracts
        the groups attribute for tag-based filtering.

        Detection Priority:
            1. Static analysis of base class inheritance (most reliable)
            2. Directory structure (/api/ or /d2d/ in path)
            3. Default to 'api' with warning

        Args:
            test_file: Path to the test file to analyze

        Returns:
            TestFileMetadata containing path, test_type, and groups list

        Example:
            ```python
            metadata = TestMetadataResolver.resolve(Path("verify_bgp.py"))
            # metadata.test_type = "d2d"
            # metadata.groups = ["health", "bgp"]
            ```
        """
        test_file = test_file.absolute()

        # Try AST-based detection first (extracts both type and groups)
        try:
            return TestMetadataResolver._extract_metadata(test_file)
        except NoRecognizedBaseError:
            logger.debug(
                f"{test_file.name}: No recognized base class, trying directory detection"
            )
        except (OSError, SyntaxError) as e:
            logger.warning(
                f"Failed to parse {test_file}: {e}, trying directory detection"
            )

        # Fall back to directory-based detection (no groups available)
        test_type = TestMetadataResolver._detect_via_directory(test_file)
        return TestFileMetadata(path=test_file, test_type=test_type, groups=[])

    @staticmethod
    def _detect_via_directory(test_file: Path) -> TestType:
        """Detect test type from directory structure.

        This is the fallback detection method when AST analysis fails to
        find a recognized base class.

        Args:
            test_file: Absolute path to the test file

        Returns:
            Test type string: "api" or "d2d"
        """
        path_str = test_file.as_posix()

        # Check for /d2d/ in path (Direct-to-Device tests)
        if "/d2d/" in path_str:
            logger.debug(f"{test_file.name}: Using directory-based detection (d2d)")
            return "d2d"

        # Check for /api/ in path (API/Controller tests)
        if "/api/" in path_str:
            logger.debug(f"{test_file.name}: Using directory-based detection (api)")
            return "api"

        # Default to 'api' with warning
        logger.warning(
            f"{test_file}: Could not detect test type from base class or directory. "
            f"Assuming '{DEFAULT_TEST_TYPE}'. To fix: inherit from a known base class or place in /d2d/ directory."
        )
        return DEFAULT_TEST_TYPE
