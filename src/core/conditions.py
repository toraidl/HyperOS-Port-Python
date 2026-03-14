"""
Condition checking strategies for device configuration rules.
Implements a flexible, extensible condition evaluation system.
"""

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Dict, List, Optional, Union
from packaging.version import parse as parse_version


class BuildContext:
    """
    Context object containing build-time information for condition evaluation.
    This is a simplified interface - actual implementation may vary.
    """

    def __init__(self):
        # ROM type flags
        self.is_port_eu_rom: bool = False
        self.is_port_global_rom: bool = False

        # Version information
        self.port_android_version: int = 0
        self.base_android_version: int = 0
        self.port_rom_version: str = ""
        self.port_os_version_incremental: str = ""

        # Region and chipset info
        self.base_regionmark: str = ""
        self.base_chipset_family: str = ""
        self.base_device_code: str = ""

        # File existence cache
        self._file_cache: Dict[str, bool] = {}

    def file_exists(self, path: str) -> bool:
        """Check if a file exists with caching."""
        if path not in self._file_cache:
            self._file_cache[path] = Path(path).exists()
        return self._file_cache[path]


class ConditionStrategy(ABC):
    """Abstract base class for condition strategies."""

    @abstractmethod
    def check(self, rule: Dict[str, Any], ctx: BuildContext) -> bool:
        """
        Evaluate the condition against the build context.

        Args:
            rule: The rule dictionary containing condition data
            ctx: Build context with runtime information

        Returns:
            True if condition passes, False otherwise
        """
        pass


class SimpleConditionStrategy(ConditionStrategy):
    """
    Handles simple, flat condition fields like:
    - condition_android_version
    - condition_is_port_eu_rom
    - condition_regionmark
    etc.
    """

    def check(self, rule: Dict[str, Any], ctx: BuildContext) -> bool:
        # ROM Type Conditions
        if rule.get("condition_is_port_eu_rom") and not ctx.is_port_eu_rom:
            return False

        # Port Android Version (exact match)
        cond_port_v = rule.get("condition_port_android_version")
        if cond_port_v is not None and int(ctx.port_android_version) != cond_port_v:
            return False

        # Base Android Version Less Than
        cond_base_lt = rule.get("condition_base_android_version_lt")
        if cond_base_lt is not None and int(ctx.base_android_version) >= cond_base_lt:
            return False

        # Base Android Version Greater Than or Equal
        cond_base_gte = rule.get("condition_base_android_version_gte")
        if cond_base_gte is not None and int(ctx.base_android_version) < cond_base_gte:
            return False

        # RegionMark (supports single string or list)
        cond_region = rule.get("condition_regionmark")
        if cond_region is not None:
            allowed_regions = [cond_region] if isinstance(cond_region, str) else cond_region
            if ctx.base_regionmark not in allowed_regions:
                return False

        # Not RegionMark
        cond_not_region = rule.get("condition_not_regionmark")
        if cond_not_region is not None and ctx.base_regionmark == cond_not_region:
            return False

        # Port OS Version Incremental Greater Than or Equal
        cond_port_os_v_gte = rule.get("condition_port_os_version_incremental_gte")
        if cond_port_os_v_gte is not None and ctx.port_os_version_incremental:
            try:
                # Remove common prefix like 'OS' for parsing if present
                v1 = str(ctx.port_os_version_incremental).replace("OS", "", 1)
                v2 = str(cond_port_os_v_gte).replace("OS", "", 1)
                if parse_version(v1) < parse_version(v2):
                    return False
            except Exception:
                # Fallback to string comparison
                if str(ctx.port_os_version_incremental) < str(cond_port_os_v_gte):
                    return False

        # Port ROM Version
        cond_port_rom_v = rule.get("condition_port_rom_version")
        if cond_port_rom_v is not None and cond_port_rom_v not in str(ctx.port_rom_version):
            return False

        # File existence condition
        cond_file = rule.get("condition_file_exists")
        if cond_file is not None and not ctx.file_exists(cond_file):
            return False

        # Target exists condition
        cond_target = rule.get("condition_target_exists")
        if cond_target is True:
            # This requires additional context about target files
            # For now, we'll skip this check if not provided
            pass

        return True


class CompositeConditionStrategy(ConditionStrategy):
    """
    Handles composite conditions with logical operators:
    {
        "and": [cond1, cond2, ...],
        "or": [cond1, cond2, ...],
        "not": cond
    }
    """

    def __init__(self, simple_strategy: Optional[ConditionStrategy] = None):
        self.simple_strategy = simple_strategy or SimpleConditionStrategy()

    def check(self, rule: Dict[str, Any], ctx: BuildContext) -> bool:
        condition = rule.get("condition")
        if condition is None:
            # Fall back to simple conditions
            return self.simple_strategy.check(rule, ctx)

        return self._evaluate_condition(condition, ctx)

    def _evaluate_condition(self, condition: Dict[str, Any], ctx: BuildContext) -> bool:
        """Recursively evaluate a condition object."""

        # AND operator - all conditions must pass
        if "and" in condition:
            conditions = condition["and"]
            if not isinstance(conditions, list):
                return False
            return all(self._evaluate_condition(c, ctx) for c in conditions)

        # OR operator - at least one condition must pass
        if "or" in condition:
            conditions = condition["or"]
            if not isinstance(conditions, list):
                return False
            return any(self._evaluate_condition(c, ctx) for c in conditions)

        # NOT operator - condition must fail
        if "not" in condition:
            return not self._evaluate_condition(condition["not"], ctx)

        # Android version range
        if "android_version" in condition or "base_android_version" in condition:
            av_cond = condition.get("android_version") or condition.get("base_android_version")
            if isinstance(av_cond, dict):
                min_v = av_cond.get("min")
                max_v = av_cond.get("max")
                current = int(ctx.base_android_version)

                if min_v is not None and current < min_v:
                    return False
                if max_v is not None and current > max_v:
                    return False
                return True

        # Port Android version range
        if "port_android_version" in condition:
            av_cond = condition["port_android_version"]
            if isinstance(av_cond, dict):
                min_v = av_cond.get("min")
                max_v = av_cond.get("max")
                current = int(ctx.port_android_version)

                if min_v is not None and current < min_v:
                    return False
                if max_v is not None and current > max_v:
                    return False
                return True

        # Region condition
        if "region" in condition:
            region_cond = condition["region"]
            if isinstance(region_cond, str):
                return ctx.base_regionmark == region_cond
            elif isinstance(region_cond, list):
                return ctx.base_regionmark in region_cond

        # ROM type condition
        if "rom_type" in condition:
            rom_type = condition["rom_type"]
            if rom_type == "ColorOS":
                return ctx.portIsColorOS
            elif rom_type == "ColorOS_Global":
                return ctx.portIsColorOSGlobal
            elif rom_type == "OxygenOS":
                return ctx.portIsOOS

        # ROM version comparison
        if "rom_version" in condition:
            rom_cond = condition["rom_version"]
            current_rom = str(ctx.port_oplusrom_version)

            if isinstance(rom_cond, dict):
                # Support operators: eq, ne, contains, starts_with, ends_with
                if "eq" in rom_cond and current_rom != rom_cond["eq"]:
                    return False
                if "ne" in rom_cond and current_rom == rom_cond["ne"]:
                    return False
                if "contains" in rom_cond and rom_cond["contains"] not in current_rom:
                    return False
                if "starts_with" in rom_cond and not current_rom.startswith(
                    rom_cond["starts_with"]
                ):
                    return False
                if "ends_with" in rom_cond and not current_rom.endswith(rom_cond["ends_with"]):
                    return False
            elif isinstance(rom_cond, str):
                return rom_cond in current_rom

        # File exists condition
        if "file_exists" in condition:
            return ctx.file_exists(condition["file_exists"])

        # Unknown condition type - default to pass with warning
        # (Could be logged in a real implementation)
        return True


class ConditionEvaluator:
    """
    Main entry point for condition evaluation.
    Combines simple and composite strategies.
    """

    def __init__(self):
        self.simple_strategy = SimpleConditionStrategy()
        self.composite_strategy = CompositeConditionStrategy(self.simple_strategy)

    def evaluate(self, rule: Dict[str, Any], ctx: BuildContext) -> bool:
        """
        Evaluate all conditions in a rule.

        Args:
            rule: Rule dictionary containing conditions
            ctx: Build context

        Returns:
            True if all conditions pass, False otherwise
        """
        # Use composite strategy which handles both simple and composite conditions
        return self.composite_strategy.check(rule, ctx)

    def evaluate_with_reason(self, rule: Dict[str, Any], ctx: BuildContext) -> tuple:
        """
        Evaluate conditions and return reason for failure.

        Args:
            rule: Rule dictionary containing conditions
            ctx: Build context

        Returns:
            Tuple of (passed: bool, reason: str)
        """
        condition = rule.get("condition")
        description = rule.get("description", "Unnamed rule")

        if condition is None:
            # Use simple strategy
            passed = self.simple_strategy.check(rule, ctx)
            if passed:
                return True, f"Rule '{description}' passed"
            else:
                return False, f"Rule '{description}' failed simple condition check"

        # For composite conditions, provide more detailed feedback
        result = self._evaluate_with_detail(condition, ctx, description)
        return result

    def _evaluate_with_detail(
        self, condition: Dict[str, Any], ctx: BuildContext, description: str, depth: int = 0
    ) -> tuple:
        """Recursively evaluate with detailed failure reasons."""
        indent = "  " * depth

        if "and" in condition:
            conditions = condition["and"]
            for i, c in enumerate(conditions):
                result, reason = self._evaluate_with_detail(c, ctx, description, depth + 1)
                if not result:
                    return False, f"{indent}AND condition [{i}] failed: {reason}"
            return True, f"{indent}All AND conditions passed"

        if "or" in condition:
            conditions = condition["or"]
            reasons = []
            for i, c in enumerate(conditions):
                result, reason = self._evaluate_with_detail(c, ctx, description, depth + 1)
                if result:
                    return True, f"{indent}OR condition [{i}] passed"
                reasons.append(reason)
            return False, f"{indent}All OR conditions failed: {'; '.join(reasons)}"

        if "not" in condition:
            result, reason = self._evaluate_with_detail(
                condition["not"], ctx, description, depth + 1
            )
            if result:
                return False, f"{indent}NOT condition failed (inner condition passed)"
            else:
                return True, f"{indent}NOT condition passed (inner condition failed)"

        # Simple condition - just evaluate
        # Create a pseudo-rule for the simple strategy
        pseudo_rule = {"condition": condition}
        passed = self.simple_strategy.check(pseudo_rule, ctx)
        if passed:
            return True, f"{indent}Condition passed"
        else:
            return False, f"{indent}Condition failed"


# Convenience function
def check_conditions(rule: Dict[str, Any], ctx: BuildContext) -> bool:
    """
    Check if a rule's conditions are satisfied.

    Args:
        rule: Rule dictionary
        ctx: Build context

    Returns:
        True if conditions pass, False otherwise
    """
    evaluator = ConditionEvaluator()
    return evaluator.evaluate(rule, ctx)


def check_conditions_verbose(rule: Dict[str, Any], ctx: BuildContext) -> tuple:
    """
    Check conditions with detailed failure reason.

    Args:
        rule: Rule dictionary
        ctx: Build context

    Returns:
        Tuple of (passed, reason)
    """
    evaluator = ConditionEvaluator()
    return evaluator.evaluate_with_reason(rule, ctx)
