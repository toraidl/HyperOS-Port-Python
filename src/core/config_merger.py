"""
Configuration merger with enhanced strategies.
Supports append, override, remove strategies and dependency resolution.
"""

import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple
from dataclasses import dataclass, field


@dataclass
class MergeReport:
    """Report generated after merging configurations."""
    loaded_files: List[str] = field(default_factory=list)
    missing_files: List[str] = field(default_factory=list)
    merged_keys: List[str] = field(default_factory=list)
    removed_items: List[str] = field(default_factory=list)
    overridden_items: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "loaded_files": self.loaded_files,
            "missing_files": self.missing_files,
            "merged_keys": self.merged_keys,
            "removed_items": self.removed_items,
            "overridden_items": self.overridden_items,
            "warnings": self.warnings,
            "errors": self.errors
        }
    
    def __str__(self) -> str:
        return json.dumps(self.to_dict(), indent=2)


class ConfigMergeError(Exception):
    """Exception raised when configuration merging fails."""
    def __init__(self, message: str, report: Optional[MergeReport] = None):
        self.message = message
        self.report = report
        super().__init__(self.message)


class ConfigMerger:
    """
    Enhanced configuration merger supporting multiple strategies:
    - append: Default, adds new items without duplicates
    - override: Replaces parent configuration entirely
    - remove: Removes items from parent configuration
    
    Also supports dependency resolution between rules.
    """
    
    MERGE_STRATEGY_APPEND = "append"
    MERGE_STRATEGY_OVERRIDE = "override"
    MERGE_STRATEGY_REMOVE = "remove"
    
    def __init__(self, logger=None):
        """
        Initialize the merger.
        
        Args:
            logger: Optional logger instance for logging messages
        """
        self.logger = logger
        self.report = MergeReport()
    
    def _log(self, level: str, message: str):
        """Log a message if logger is available."""
        if self.logger:
            getattr(self.logger, level, print)(message)
        else:
            print(f"[{level.upper()}] {message}")
    
    def merge(self, base: Dict[str, Any], extra: Dict[str, Any], 
              path: str = "") -> Dict[str, Any]:
        """
        Merge two configurations with strategy support.
        
        Args:
            base: Base configuration
            extra: Extra configuration to merge into base
            path: Current path for logging
        
        Returns:
            Merged configuration
        """
        result = base.copy()
        
        for key, value in extra.items():
            current_path = f"{path}.{key}" if path else key
            
            if key not in result:
                # New key, just add it
                result[key] = value
            else:
                base_value = result[key]
                
                # Check for merge strategy hint
                if isinstance(value, dict):
                    strategy = value.get("merge_strategy")
                    
                    if strategy == self.MERGE_STRATEGY_OVERRIDE:
                        # Override: replace base value entirely
                        result[key] = value
                        self.report.overridden_items.append(current_path)
                        self._log("debug", f"Override: {current_path}")
                        continue
                    
                    elif strategy == self.MERGE_STRATEGY_REMOVE:
                        # Remove: remove from base
                        remove_by = value.get("remove_by_description")
                        if remove_by and isinstance(base_value, list):
                            result[key] = [
                                item for item in base_value 
                                if item.get("description") != remove_by
                            ]
                            self.report.removed_items.append(current_path)
                            self._log("debug", f"Remove: {current_path} (by description: {remove_by})")
                        continue
                    
                    # Remove internal strategy key from result
                    value = {k: v for k, v in value.items() if k != "merge_strategy"}
                
                # Deep merge based on type
                result[key] = self._deep_merge(base_value, value, current_path)
        
        return result
    
    def _deep_merge(self, base: Any, extra: Any, path: str = "") -> Any:
        """
        Deep merge two values.
        
        Args:
            base: Base value
            extra: Value to merge into base
            path: Current path for logging
        
        Returns:
            Merged value
        """
        if isinstance(base, dict) and isinstance(extra, dict):
            return self.merge(base, extra, path)
        
        elif isinstance(base, list) and isinstance(extra, list):
            # For lists of dicts with 'description' field, merge by description
            if base and isinstance(base[0], dict) and "description" in base[0]:
                return self._merge_list_by_description(base, extra, path)
            
            # Simple deduplication for other lists
            result = base.copy()
            for item in extra:
                if item not in result:
                    result.append(item)
            return result
        
        elif isinstance(base, dict) and isinstance(extra, dict):
            return self.merge(base, extra, path)
        
        # For primitives, extra overrides base
        return extra
    
    def _merge_list_by_description(self, base: List[Dict], extra: List[Dict], 
                                    path: str = "") -> List[Dict]:
        """
        Merge two lists of dictionaries by matching 'description' field.
        
        Args:
            base: Base list
            extra: List to merge
            path: Current path for logging
        
        Returns:
            Merged list
        """
        result = base.copy()
        
        # Create index by description
        desc_to_index = {
            item.get("description"): i 
            for i, item in enumerate(result) 
            if "description" in item
        }
        
        for extra_item in extra:
            description = extra_item.get("description")
            
            # Check for remove strategy
            if extra_item.get("merge_strategy") == self.MERGE_STRATEGY_REMOVE:
                if description and description in desc_to_index:
                    idx = desc_to_index[description]
                    removed = result.pop(idx)
                    self.report.removed_items.append(f"{path}[{description}]")
                    self._log("debug", f"Removed from {path}: {description}")
                    
                    # Rebuild index after removal
                    desc_to_index = {
                        item.get("description"): i 
                        for i, item in enumerate(result) 
                        if "description" in item
                    }
                continue
            
            # Check for override strategy
            if extra_item.get("merge_strategy") == self.MERGE_STRATEGY_OVERRIDE:
                if description and description in desc_to_index:
                    idx = desc_to_index[description]
                    result[idx] = extra_item
                    self.report.overridden_items.append(f"{path}[{description}]")
                    self._log("debug", f"Overridden in {path}: {description}")
                    continue
            
            # Default: append if not exists, merge if exists
            if description and description in desc_to_index:
                # Merge with existing item
                idx = desc_to_index[description]
                result[idx] = self._deep_merge(
                    result[idx], 
                    extra_item, 
                    f"{path}[{description}]"
                )
            else:
                # New item, append
                result.append(extra_item)
                self._log("debug", f"Added to {path}: {description or 'unnamed'}")
        
        return result
    
    def load_and_merge(self, paths: List[Path], filename: str) -> Tuple[Dict[str, Any], MergeReport]:
        """
        Load and merge multiple configuration files.
        
        Args:
            paths: List of directory paths to scan
            filename: Name of the config file to load from each directory
        
        Returns:
            Tuple of (merged_config, merge_report)
        """
        self.report = MergeReport()
        config = {}
        
        for p in paths:
            file_path = p / filename
            if file_path.exists():
                try:
                    with open(file_path, 'r') as f:
                        data = json.load(f)
                    
                    self.report.loaded_files.append(str(file_path))
                    
                    if not config:
                        config = data
                    else:
                        config = self.merge(config, data, str(file_path))
                    
                    self._log("info", f"Loaded and merged config from {file_path}")
                except json.JSONDecodeError as e:
                    error_msg = f"Invalid JSON in {file_path}: {e}"
                    self.report.errors.append(error_msg)
                    self._log("error", error_msg)
                except Exception as e:
                    error_msg = f"Failed to load config {file_path}: {e}"
                    self.report.errors.append(error_msg)
                    self._log("error", error_msg)
            else:
                self.report.missing_files.append(str(file_path))
                self._log("debug", f"Config file not found: {file_path} (this may be expected)")
        
        # Extract merged keys
        if config:
            self.report.merged_keys = list(config.keys())
        
        return config, self.report
    
    def resolve_dependencies(self, rules: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Resolve dependencies between rules using topological sort.
        
        Args:
            rules: List of rules that may have 'depends_on' and 'id' fields
        
        Returns:
            Rules in dependency order
        
        Raises:
            ConfigMergeError: If circular dependencies are detected
        """
        # Build dependency graph
        id_to_rule = {}
        for rule in rules:
            rule_id = rule.get("id")
            if rule_id:
                id_to_rule[rule_id] = rule
        
        # Find rules with dependencies
        has_deps = set()
        dep_graph: Dict[str, Set[str]] = {}
        
        for rule in rules:
            rule_id = rule.get("id", id(rule))  # Use id() as fallback
            depends_on = rule.get("depends_on", [])
            
            if depends_on:
                has_deps.add(rule_id)
                dep_graph[rule_id] = set(depends_on)
        
        if not has_deps:
            return rules  # No dependencies to resolve
        
        # Topological sort using Kahn's algorithm
        # Calculate in-degree
        in_degree: Dict[str, int] = {rule.get("id", id(rule)): 0 for rule in rules}
        
        for rule_id, deps in dep_graph.items():
            for dep in deps:
                if dep in in_degree:
                    pass  # Dependency exists
                else:
                    self.report.warnings.append(
                        f"Rule '{rule_id}' depends on unknown rule '{dep}'"
                    )
        
        for rule_id, deps in dep_graph.items():
            for dep in deps:
                if dep in in_degree:
                    in_degree[rule_id] = in_degree.get(rule_id, 0) + 1
        
        # Start with rules that have no dependencies
        queue = [rid for rid, deg in in_degree.items() if deg == 0]
        result = []
        
        while queue:
            # Sort for deterministic order
            queue.sort()
            current = queue.pop(0)
            
            # Find the actual rule
            rule = id_to_rule.get(current)
            if rule:
                result.append(rule)
            else:
                # Rule without id
                for r in rules:
                    if id(r) == current and r not in result:
                        result.append(r)
                        break
            
            # Reduce in-degree for dependent rules
            for rule_id, deps in dep_graph.items():
                if current in deps:
                    in_degree[rule_id] -= 1
                    if in_degree[rule_id] == 0:
                        queue.append(rule_id)
        
        # Check for circular dependencies
        if len(result) < len(rules):
            remaining = [r for r in rules if r not in result]
            remaining_ids = [r.get("id", str(id(r))) for r in remaining]
            raise ConfigMergeError(
                f"Circular dependency detected involving: {remaining_ids}",
                self.report
            )
        
        return result


def merge_configs(paths: List[Path], filename: str, logger=None) -> Tuple[Dict[str, Any], MergeReport]:
    """
    Convenience function to load and merge configs.
    
    Args:
        paths: List of paths to load
        filename: Config filename for reporting
        logger: Optional logger
    
    Returns:
        Tuple of (merged_config, merge_report)
    """
    merger = ConfigMerger(logger)
    return merger.load_and_merge(paths, filename)


def resolve_rule_dependencies(rules: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Convenience function to resolve rule dependencies.
    
    Args:
        rules: List of rules
    
    Returns:
        Rules in dependency order
    """
    merger = ConfigMerger()
    return merger.resolve_dependencies(rules)
