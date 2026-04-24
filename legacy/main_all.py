#!/usr/bin/env python3
# type: ignore - Disable type checking for this entire file due to dynamic imports and configurations
"""
Script pour exécuter la découverte de règles sur toutes les bases de données dans un répertoire.

Ce script traite automatiquement toutes les bases de données SQLite (.db) trouvées dans un répertoire
spécifié et exécute l'algorithme de découverte de règles sur chacune d'elles.

Usage:
    python main_all.py [--config config.yaml] [--db-dir path/to/databases] [--algorithm ALGORITHM_NAME]
"""

import argparse
import threading
import shutil
import datetime
import signal
import sys
import logging
import os
import subprocess
import time
from contextlib import contextmanager
from pathlib import Path
from typing import List, Optional, Dict, Any, Union, cast
import glob
import concurrent.futures

try:
    import mlflow  # type: ignore
    mlflow_available = True
except ImportError:
    mlflow_available = False
    mlflow = None

from mahilda.algorithms.base_algorithm import BaseAlgorithm
from mahilda.algorithms.amie3 import Amie3
from mahilda.algorithms.ilp import ILP
from mahilda.algorithms.spider import Spider
from mahilda.algorithms.mahilda import MAHILDA

from mahilda.database.alchemy_utility import AlchemyUtility
from mahilda.utils.logging_utils import configure_global_logger
from mahilda.utils.monitor import ResourceMonitor
from mahilda.utils.config_loader import load_config  # type: ignore
from mahilda.utils.rules import RuleIO
from mahilda.utils.structure_analysis import (
    analyze_rule_structure as compute_structure_analysis,
    log_structure_analysis as log_structure_analysis_util,
    save_pattern_2_2_rules as save_pattern_2_2_rules_util,
)
        return compute_structure_analysis(rules)
        description="Execute rule discovery on all databases in a specified directory."
    )
        log_structure_analysis_util(self.logger, analysis, database_name)
    
    Args:
        save_pattern_2_2_rules_util(structure_analysis, db_results_dir, db_stem, logger=self.logger)
    """Read blacklist file and return list of stems (one per line)."""
    stems: List[str] = []
    try:
        p = Path(file_path)
        if not p.exists():
            return stems
        with p.open('r', encoding='utf-8') as f:
            for raw in f:
                line = raw.strip()
                if not line or line.startswith('#'):
                    continue
                # remove .db suffix if provided
                if line.endswith('.db'):
                    line = Path(line).stem
                stems.append(line)
    except Exception:
        return []

    return stems


# ---------------------------------------------------------------------------
# Hard-coded blacklist
# Add database stems here to always skip them. These entries are merged with
# CLI/config/file-provided blacklist values. Edit this list to add/remove DBs.
# Example: HARD_CODED_BLACKLIST = ["db_to_skip_1", "db_to_skip_2"]
# ---------------------------------------------------------------------------
HARD_CODED_BLACKLIST: List[str] = ["voc","walmart","world",
                                   "university","restbase","northwind",
                                   "trains","cs","classicmodels"]


def initialize_directories(results_dir: Path, log_dir: Path) -> None:
    """
    Ensures that results and logs directories exist.
    """
    results_dir.mkdir(parents=True, exist_ok=True)
    log_dir.mkdir(parents=True, exist_ok=True)


def file_nonempty(p: Path) -> bool:
    try:
        return p.exists() and p.is_file() and p.stat().st_size > 0
    except Exception:
        return False


class DatabaseBatchProcessor:
    """Handles batch processing of multiple databases for rule discovery."""

    def __init__(
        self,
        algorithm_name: str,
        database_path: Path,
        results_dir: Path,
        logger: logging.Logger,
        use_mlflow: bool = False,
        config: Optional[Dict[str, Any]] = None,
        resource_monitor: Optional[ResourceMonitor] = None,
        force_rerun: bool = False,
    ):
        self.algorithm_name = algorithm_name
        self.database_path = database_path
        self.results_dir = results_dir
        self.logger = logger
        self.use_mlflow = use_mlflow
        self.config = config or {}
        self.resource_monitor = resource_monitor
        self.force_rerun = force_rerun
        self.processed_databases: List[Dict[str, Any]] = []
        self.failed_databases: List[Dict[str, Any]] = []
        self.total_rules = 0

        # Global structure analysis
        self.global_structure_stats = {
            'total_rules_all_dbs': 0,
            'total_pattern_2_2_all_dbs': 0,
            'databases_with_pattern_2_2': 0,
            'avg_accuracy_all_rules': 0,
            'avg_confidence_all_rules': 0
        }

    def validate_foreign_keys(self, db_util: AlchemyUtility) -> bool:
        """
        Validate foreign key constraints in the database.
        
        Args:
            db_util: Database utility instance
            
        Returns:
            True if all foreign keys are valid, False otherwise
        """
        try:
            # Get all foreign key relationships using the built-in query_utility
            foreign_keys = db_util.query_utility._get_foreign_keys()
            
            if not foreign_keys:
                self.logger.info("No foreign key relationships found in database")
                return True
            
            self.logger.info(f"Validating foreign key relationships in {len(foreign_keys)} tables...")
            
            validation_errors = []
            total_fks = 0
            
            for table_name, fk_dict in foreign_keys.items():
                for column_name, (ref_table, ref_column) in fk_dict.items():
                    total_fks += 1
                    
                    # Check if referenced table exists
                    table_names = db_util.get_table_names()
                    if ref_table not in table_names:
                        error_msg = f"Referenced table '{ref_table}' does not exist for foreign key '{table_name}.{column_name}'"
                        validation_errors.append(error_msg)
                        continue
                    
                    # Check if referenced column exists
                    column_names = db_util.get_attribute_names(ref_table)
                    if ref_column not in column_names:
                        error_msg = f"Referenced column '{ref_table}.{ref_column}' does not exist for foreign key '{table_name}.{column_name}'"
                        validation_errors.append(error_msg)
                        continue
            
            self.logger.info(f"Total foreign keys validated: {total_fks}")
            
            if validation_errors:
                self.logger.warning(f"Found {len(validation_errors)} foreign key validation errors:")
                for error in validation_errors[:10]:  # Show first 10 errors
                    self.logger.warning(f"  - {error}")
                if len(validation_errors) > 10:
                    self.logger.warning(f"  ... and {len(validation_errors) - 10} more errors")
                return False
            else:
                self.logger.info("All foreign key relationships are valid")
                return True
                
        except Exception as e:
            self.logger.error(f"Error validating foreign keys: {e}")
            return False

    def process_single_database(self, db_file: Path) -> Dict[str, Any]:
        """Process a single database and return results."""
        database_name = db_file.name
        db_stem = db_file.stem

        self.logger.info(f"Starting rule discovery for database: {database_name}")

        # Create database-specific result directory
        db_results_dir = self.results_dir / db_stem
        db_results_dir.mkdir(parents=True, exist_ok=True)

        # Early-stop check: if results already exist and not forcing rerun, skip processing
        json_file_name = f"{self.algorithm_name}_{db_stem}_results.json"
        report_file_name = f"report_{self.algorithm_name}_{db_stem}.md"

        # Current design paths (results/<DB>/...)
        result_path = db_results_dir / json_file_name
        report_path = db_results_dir / report_file_name

        # Legacy design paths (results/<ALGO_DB>/...) and top-level report
        legacy_dir = self.results_dir / f"{self.algorithm_name}_{db_stem}"
        legacy_json_path = legacy_dir / json_file_name
        legacy_report_in_dir = legacy_dir / report_file_name
        top_level_report = self.results_dir / report_file_name

        def _file_nonempty(p: Path) -> bool:
            try:
                return p.exists() and p.is_file() and p.stat().st_size > 0
            except Exception:
                return False

        candidates = [result_path, report_path, legacy_json_path, legacy_report_in_dir, top_level_report]
        existing = [p for p in candidates if _file_nonempty(p)]

        if not self.force_rerun and existing:
            rules_count = None
            # Try to infer rules_count from JSON if present
            json_candidates = [result_path, legacy_json_path]
            json_existing = next((p for p in json_candidates if p.exists()), None)
            if json_existing is not None:
                try:
                    import json as _json
                    with json_existing.open('r', encoding='utf-8') as f:
                        data = _json.load(f)
                    if isinstance(data, list):
                        rules_count = len(data)
                    elif isinstance(data, dict):
                        if isinstance(data.get('rules'), list):
                            rules_count = len(data['rules'])
                        elif isinstance(data.get('number_of_rules'), int):
                            rules_count = int(data['number_of_rules'])
                except Exception:
                    rules_count = None
            # Fallback: try to parse the report for the number
            report_candidates = [report_path, legacy_report_in_dir, top_level_report]
            report_existing = next((p for p in report_candidates if p.exists()), None)
            if rules_count is None and report_existing is not None:
                try:
                    import re as _re
                    with report_existing.open('r', encoding='utf-8') as rf:
                        for line in rf:
                            if "Number of Rules Discovered:" in line:
                                m = _re.search(r"Number of Rules Discovered:\s*(\d+)", line)
                                if m:
                                    rules_count = int(m.group(1))
                                    break
                except Exception:
                    rules_count = None
            if rules_count is None:
                rules_count = 0

            self.logger.info(
                f"Early-stop: results already exist for {database_name}. Skipping. Use --force to rerun."
            )
            result = {
                "database": database_name,
                "rules_count": rules_count,
                "result_path": existing[0],
                "status": "skipped",
                "structure_analysis": None,
                "time_taken": 0,
                "timeout": False,
            }
            self.processed_databases.append(result)
            self.total_rules += rules_count
            return result

        # Initialize timeout tracking
        start_time = time.time()
        max_processing_time = 3600  # 1 hour per database
        time_taken = None
        killed_by_resource_monitor = False

        try:
            with mlflow_run_context(self.use_mlflow, self.config, db_stem):
                algorithm_map = {
                    "POPPER": ILP,
                    "ILP": ILP,
                    "AMIE3": Amie3,
                    "SPIDER": Spider,
                    "MAHILDA": MAHILDA,
                }
                selected_algorithm = algorithm_map.get(self.algorithm_name.upper(), MAHILDA)

                db_uri = f"sqlite:///{db_file}"
                self.logger.info(f"Using database URI: {db_uri}")

                with AlchemyUtility(db_uri, database_path=str(self.database_path), create_index=False) as db_util:
                    # Check timeout before processing
                    if time.time() - start_time > max_processing_time:
                        raise TimeoutError(f"Processing timeout ({max_processing_time}s) exceeded for {database_name}")

                    # Validate foreign keys before processing
                    self.logger.info(f"Validating foreign keys for database: {database_name}")
                    fk_valid = self.validate_foreign_keys(db_util)
                    if not fk_valid:
                        self.logger.warning(f"Foreign key validation failed for {database_name}, but continuing with processing...")

                    # Check timeout again after validation
                    if time.time() - start_time > max_processing_time:
                        raise TimeoutError(f"Processing timeout ({max_processing_time}s) exceeded for {database_name}")

                    algo: BaseAlgorithm = selected_algorithm(db_util)
                    rules = []

                    self.logger.debug(f"Starting rule discovery for {database_name}...")

                    # Monitor resource usage et timeout pendant la découverte des règles
                    for rule in algo.discover_rules(
                        results_dir=str(db_results_dir),
                        should_stop=lambda: (
                            (time.time() - start_time > max_processing_time)
                            or (self.resource_monitor and getattr(self.resource_monitor, 'should_stop', False))
                        ),
                        timeout=max_processing_time,
                    ):
                        # Check timeout periodically during rule discovery
                        if time.time() - start_time > max_processing_time:
                            self.logger.warning(f"Timeout reached for {database_name}, stopping rule discovery")
                            killed_by_resource_monitor = True  # Treat as timeout-triggered stop
                            break

                        # Check resource monitor if available
                        if self.resource_monitor and hasattr(self.resource_monitor, 'should_stop'):
                            if self.resource_monitor.should_stop:
                                self.logger.warning(f"Resource monitor triggered stop for {database_name}")
                                killed_by_resource_monitor = True
                                break

                        self.logger.info(f"Discovered rule: {rule}")
                        rules.append(rule)

                    # If we were stopped by monitor or timeout, return immediately without heavy post-processing
                    if killed_by_resource_monitor or (self.resource_monitor and getattr(self.resource_monitor, 'should_stop', False)):
                        time_taken = time.time() - start_time
                        result = {
                            "database": database_name,
                            "rules_count": len(rules),
                            "result_path": None,
                            "status": "timeout",
                            "structure_analysis": None,
                            "time_taken": time_taken,
                            "timeout": True
                        }
                        self.processed_databases.append(result)
                        self.total_rules += len(rules)
                        return result

                    time_taken = time.time() - start_time

                    json_file_name = f"{self.algorithm_name}_{db_stem}_results.json"
                    result_path = db_results_dir / json_file_name

                    self.logger.debug(f"Saving rules to {result_path}")
                    number_of_rules = RuleIO.save_rules_to_json(rules, str(result_path))

                    self.logger.info(f"Discovered {number_of_rules} rules for {database_name}.")

                    # Analyze rule structure
                    structure_analysis = self.analyze_rule_structure(rules)
                    self.log_structure_analysis(structure_analysis, database_name)

                    # Save pattern 2+2 rules if found
                    if structure_analysis['pattern_analysis']['pattern_2_2'] > 0:
                        self.save_pattern_2_2_rules(structure_analysis, db_results_dir, db_stem)

                    # Generate report
                    if self.algorithm_name.upper() == "SPIDER":
                        self.generate_database_report(db_stem, number_of_rules, result_path, [], db_results_dir, structure_analysis, time_taken, killed_by_resource_monitor)
                    else:
                        try:
                            top_rules = sorted(rules, key=lambda x: -x.accuracy)[:5]
                        except AttributeError:
                            top_rules = rules[:5]  # Fallback if accuracy attribute doesn't exist
                        self.generate_database_report(db_stem, number_of_rules, result_path, top_rules, db_results_dir, structure_analysis, time_taken, killed_by_resource_monitor)

                    if self.use_mlflow and mlflow_available and mlflow is not None:
                        try:
                            mlflow.log_param("algorithm", self.algorithm_name)  # type: ignore
                            mlflow.log_param("database", database_name)  # type: ignore
                            mlflow.log_metric("number_of_rules", number_of_rules)  # type: ignore
                            if time_taken is not None:
                                mlflow.log_metric("time_taken_seconds", time_taken)  # type: ignore
                            if killed_by_resource_monitor:
                                mlflow.log_param("run_status", "timeout")  # type: ignore
                        except Exception as e:
                            self.logger.warning(f"Error logging to MLflow: {e}")

                    result = {
                        "database": database_name,
                        "rules_count": number_of_rules,
                        "result_path": result_path,
                        "status": "success" if not killed_by_resource_monitor else "timeout",
                        "structure_analysis": structure_analysis,
                        "time_taken": time_taken,
                        "timeout": killed_by_resource_monitor
                    }

                    # Update global statistics
                    self.global_structure_stats['total_rules_all_dbs'] += number_of_rules
                    self.global_structure_stats['total_pattern_2_2_all_dbs'] += structure_analysis['pattern_analysis']['pattern_2_2']
                    if structure_analysis['pattern_analysis']['pattern_2_2'] > 0:
                        self.global_structure_stats['databases_with_pattern_2_2'] += 1

                    self.processed_databases.append(result)
                    self.total_rules += number_of_rules

                    return result

        except Exception as e:
            error_msg = f"Error processing database {database_name}: {e}"
            self.logger.error(error_msg, exc_info=True)

            if self.use_mlflow and mlflow_available and mlflow is not None:
                try:
                    mlflow.log_param("error", str(e))  # type: ignore
                except Exception as mlflow_error:
                    self.logger.warning(f"Error logging to MLflow: {mlflow_error}")

            result = {
                "database": database_name,
                "error": str(e),
                "status": "timeout" if isinstance(e, TimeoutError) or (self.resource_monitor and getattr(self.resource_monitor, 'should_stop', False)) else "failed",
                "time_taken": time.time() - start_time if start_time else None,
                "timeout": True if isinstance(e, TimeoutError) or (self.resource_monitor and getattr(self.resource_monitor, 'should_stop', False)) else False
            }
            self.failed_databases.append(result)
            return result

        finally:
            # Clean up for this database
            self.clean_up()

    def process_all_databases(self, db_files: List[Path], parallel: bool = False, timeout: int = 3600) -> None:
        """Process all databases with a timeout for each."""
        total_databases = len(db_files)
        self.logger.info(f"Starting batch processing of {total_databases} databases")
        if parallel:
            self.logger.warning("Parallel processing not yet implemented, falling back to sequential")
        for i, db_file in enumerate(db_files, 1):
            self.logger.info(f"Processing database {i}/{total_databases}: {db_file.name}")
            
            # Reset resource monitor timer for each database
            if self.resource_monitor:
                self.resource_monitor.reset_timer()
                self.resource_monitor.should_stop = False
            
            try:
                # Traitement direct sans ProcessPoolExecutor pour éviter les problèmes de synchronisation
                result = self.process_single_database(db_file)
                if result.get("status") == "success":
                    self.logger.info(f"Successfully processed {db_file.name}: {result.get('rules_count', 0)} rules")
                else:
                    self.logger.warning(f"Failed to process {db_file.name}: {result.get('error', 'Unknown error')}")
            except Exception as e:
                self.logger.error(f"Error processing {db_file.name}: {e}")
                self.failed_databases.append({
                    "database": db_file.name,
                    "error": str(e),
                    "status": "failed"
                })
        
        # Calculer les totaux pour le message final
        total_processed = len(self.processed_databases)
        total_failed = len(self.failed_databases)
        total_databases = total_processed + total_failed
        
        self.logger.info(f"Batch processing completed: {total_processed}/{total_databases} databases processed successfully")
        self.generate_summary_report()

    def clean_up(self, temp_dirs: Optional[List[Path]] = None) -> None:
        """Cleans up temporary directories."""
        temp_dirs = temp_dirs or [
            self.database_path / "prolog_tmp",
            self.database_path / "SPIDER_temp",
            self.database_path / "popper",
        ]
        for directory in temp_dirs:
            if directory.exists() and directory.is_dir():
                shutil.rmtree(directory)
                self.logger.debug(f"Cleaned up temporary directory: {directory}")

    def generate_database_report(self, db_stem: str, number_of_rules: int, result_path: Path, 
                                 top_rules: List[Any], db_results_dir: Path, structure_analysis: Optional[Dict[str, Any]] = None, time_taken: float = None, killed_by_resource_monitor: bool = False) -> None:
        """Generates a report for a single database."""
        # Format time taken as H:MM:SS
        time_taken_str = None
        if time_taken is not None:
            hours, rem = divmod(int(time_taken), 3600)
            minutes, seconds = divmod(rem, 60)
            time_taken_str = f"{hours}:{minutes:02d}:{seconds:02d}"
        else:
            time_taken_str = "N/A"

        status_str = "Timeout (killed by resource monitor)" if killed_by_resource_monitor else "Success"

        report_content = f"""
# Rule Discovery Report - {db_stem}

**Date:** {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
**Algorithm:** {self.algorithm_name}
**Database:** {db_stem}.db
**Number of Rules Discovered:** {number_of_rules}
**Results Path:** {result_path}
**Time Taken:** {time_taken_str}
**Status:** {status_str}

## Summary
- **Algorithm:** {self.algorithm_name}
- **Database:** {db_stem}.db
- **Number of Rules Discovered:** {number_of_rules}
- **Results Path:** {result_path}
- **Time Taken:** {time_taken_str}
- **Status:** {status_str}

## Rule Structure Analysis
"""

        if structure_analysis:
            total_rules = structure_analysis['total_rules']
            patterns = structure_analysis['pattern_analysis']
            acc_stats = structure_analysis['accuracy_stats']
            conf_stats = structure_analysis['confidence_stats']
            total_rules=max(1, total_rules)  # Avoid division by zero
            report_content += f"""
### Pattern Distribution
- **1 body → 1 head:** {patterns['pattern_1_1']} ({patterns['pattern_1_1']/total_rules*100:.1f}%)
- **1 body → 2 head:** {patterns['pattern_1_2']} ({patterns['pattern_1_2']/total_rules*100:.1f}%)
- **2 body → 1 head:** {patterns['pattern_2_1']} ({patterns['pattern_2_1']/total_rules*100:.1f}%)
- **2 body → 2 head:** {patterns['pattern_2_2']} ({patterns['pattern_2_2']/total_rules*100:.1f}%) **[TARGET PATTERN]**
- **2+ body → 2+ head:** {patterns['pattern_2_plus']} ({patterns['pattern_2_plus']/total_rules*100:.1f}%)
- **Other patterns:** {patterns['other_patterns']} ({patterns['other_patterns']/total_rules*100:.1f}%)

### Quality Distribution
- **Perfect accuracy (1.0):** {acc_stats['perfect_accuracy']} ({acc_stats['perfect_accuracy']/total_rules*100:.1f}%)
- **High accuracy (≥0.9):** {acc_stats['high_accuracy']} ({acc_stats['high_accuracy']/total_rules*100:.1f}%)
- **Perfect confidence (1.0):** {conf_stats['perfect_confidence']} ({conf_stats['perfect_confidence']/total_rules*100:.1f}%)
- **High confidence (≥0.9):** {conf_stats['high_confidence']} ({conf_stats['high_confidence']/total_rules*100:.1f}%)

### Quantifier Distribution
- **Universal (∀):** {structure_analysis['universal_quantifiers']} ({structure_analysis['universal_quantifiers']/total_rules*100:.1f}%)
- **Existential (∃):** {structure_analysis['existential_quantifiers']} ({structure_analysis['existential_quantifiers']/total_rules*100:.1f}%)
"""

            # Add special section for 2+2 pattern rules
            if patterns['pattern_2_2'] > 0:
                report_content += f"""

### 🎯 "If a and b then c and d" Pattern Rules ({patterns['pattern_2_2']} found)

This database contains **{patterns['pattern_2_2']} rules** following the target pattern of exactly 2 predicates in the body and 2 predicates in the head.

#### Top Examples:
"""
                top_2_2_rules = sorted(
                    structure_analysis['pattern_2_2_rules'], 
                    key=lambda x: (x['accuracy'], x['confidence']), 
                    reverse=True
                )[:5]

                for i, rule in enumerate(top_2_2_rules, 1):
                    rule_display = rule['display'].replace('\n', ' ').replace('|', '\\|')
                    report_content += f"""
**{i}.** {rule_display}
- Accuracy: {rule['accuracy']:.3f}
- Confidence: {rule['confidence']:.3f}
"""

        report_content += f"""

## Top 5 Best Rules
Below are the top-5 best rules discovered based on their scores:

| Rank | Rule Description | Support  | Confidence |
|------|------------------|----------| -----------|
"""

        # Add top-5 rules to the report
        for idx, rule in enumerate(top_rules, start=1):
            try:
                rule_desc = rule.display.replace('\n', ' ').replace('|', '\\|')  # Escape pipes for markdown tables
                accuracy = getattr(rule, 'accuracy', 0.0)
                confidence = getattr(rule, 'confidence', 0.0)
                report_content += f"| {idx} | {rule_desc} | {accuracy:.3f} | {confidence:.3f} |\n"
            except AttributeError:
                report_content += f"| {idx} | {str(rule)} | N/A | N/A |\n"

        report_content += f"""

## Details
The rule discovery process was completed for database {db_stem}.db. 
The discovered rules have been saved to the specified results path.
"""

        report_file_name = f"report_{self.algorithm_name}_{db_stem}.md"
        report_path = db_results_dir / report_file_name

        with report_path.open('w') as report_file:
            report_file.write(report_content)

        self.logger.info(f"Generated report: {report_path}")

        if self.use_mlflow and mlflow_available and mlflow is not None:
            try:
                mlflow.log_artifact(str(report_path))  # type: ignore
            except Exception as e:
                self.logger.warning(f"Error logging artifact to MLflow: {e}")

    def generate_summary_report(self) -> None:
        """Generate a summary report for all processed databases."""
        total_processed = len(self.processed_databases)
        total_failed = len(self.failed_databases)
        total_databases = total_processed + total_failed

        # Calculate global structure statistics
        if total_processed > 0:
            self.global_structure_stats['total_rules_all_dbs'] = sum(result['rules_count'] for result in self.processed_databases)
            self.global_structure_stats['databases_with_pattern_2_2'] = len([1 for result in self.processed_databases if result['rules_count'] > 0])
            self.global_structure_stats['total_pattern_2_2_all_dbs'] = sum(
                result.get('structure_analysis', {}).get('pattern_analysis', {}).get('pattern_2_2', 0) for result in self.processed_databases
            )
            self.global_structure_stats['avg_accuracy_all_rules'] = (
                self.global_structure_stats['total_rules_all_dbs'] / total_processed
            )
            self.global_structure_stats['avg_confidence_all_rules'] = (
                self.global_structure_stats['total_rules_all_dbs'] / total_processed
            )

        report_content = f"""
# Batch Rule Discovery Summary Report

**Date:** {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
**Algorithm:** {self.algorithm_name}
**Total Databases Processed:** {total_databases}
**Successful:** {total_processed}
**Failed:** {total_failed}
**Total Rules Discovered:** {self.total_rules}

## Processing Summary

### Successful Databases ({total_processed})
| Database | Rules Count | Result Path | Time Taken | Status |
|----------|-------------|-------------|------------|--------|
"""

        for result in self.processed_databases:
            # Format time taken
            t = result.get('time_taken', None)
            if t is not None:
                hours, rem = divmod(int(t), 3600)
                minutes, seconds = divmod(rem, 60)
                t_str = f"{hours}:{minutes:02d}:{seconds:02d}"
            else:
                t_str = "N/A"
            status = result.get('status', 'success')
            if result.get('timeout', False):
                status = 'timeout'
            report_content += f"| {result['database']} | {result['rules_count']} | {result['result_path']} | {t_str} | {status} |\n"

        if self.failed_databases:
            report_content += f"""

### Failed Databases ({total_failed})
| Database | Error | Time Taken |
|----------|-------|------------|
"""
            for result in self.failed_databases:
                error_msg = str(result.get('error', '')).replace('\n', ' ').replace('|', '\\|')
                t = result.get('time_taken', None)
                if t is not None:
                    hours, rem = divmod(int(t), 3600)
                    minutes, seconds = divmod(rem, 60)
                    t_str = f"{hours}:{minutes:02d}:{seconds:02d}"
                else:
                    t_str = "N/A"
                report_content += f"| {result['database']} | {error_msg} | {t_str} |\n"

        if total_databases > 0:
            success_rate = (total_processed / total_databases * 100)
            avg_rules = (self.total_rules / total_processed) if total_processed > 0 else 0
        else:
            success_rate = 0.0
            avg_rules = 0.0

        report_content += f"""

## Statistics
- **Success Rate:** {success_rate:.1f}%
- **Average Rules per Database:** {avg_rules:.1f}
- **Total Processing Time:** Completed at {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}

## Global Structure Statistics
- **Total Rules (all databases):** {self.global_structure_stats['total_rules_all_dbs']}
- **Databases with 2+2 Pattern Rules:** {self.global_structure_stats['databases_with_pattern_2_2']}
- **Total 2+2 Pattern Rules (all databases):** {self.global_structure_stats['total_pattern_2_2_all_dbs']}
- **Average Accuracy of All Rules:** {self.global_structure_stats['avg_accuracy_all_rules']:.3f}
- **Average Confidence of All Rules:** {self.global_structure_stats['avg_confidence_all_rules']:.3f}

## Details
This batch processing session has been completed. Individual database reports can be found in their respective result directories.
"""

        summary_file_name = f"batch_summary_{self.algorithm_name}_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.md"
        summary_path = self.results_dir / summary_file_name

        with summary_path.open('w') as report_file:
            report_file.write(report_content)

        self.logger.info(f"Generated batch summary report: {summary_path}")
        self.logger.info(f"Batch processing completed: {total_processed}/{total_databases} databases processed successfully")
        self.logger.info(f"Total rules discovered across all databases: {self.total_rules}")

    def analyze_rule_structure(self, rules: List[Any]) -> Dict[str, Any]:
        """
        Analyze the structure of discovered rules to identify patterns.
        
        Args:
            rules: List of discovered rules
            
        Returns:
            Dictionary with structure analysis results
        """
        structure_analysis = {
            'total_rules': len(rules),
            'pattern_analysis': {
                'pattern_1_1': 0,  # 1 body, 1 head
                'pattern_1_2': 0,  # 1 body, 2 head  
                'pattern_2_1': 0,  # 2 body, 1 head
                'pattern_2_2': 0,  # 2 body, 2 head (target pattern)
                'pattern_2_plus': 0,  # 2+ body, 2+ head
                'other_patterns': 0
            },
            'rule_types': {},
            'accuracy_stats': {
                'perfect_accuracy': 0,
                'high_accuracy': 0,  # >= 0.9
                'medium_accuracy': 0,  # >= 0.7
                'low_accuracy': 0  # < 0.7
            },
            'confidence_stats': {
                'perfect_confidence': 0,
                'high_confidence': 0,  # >= 0.9
                'medium_confidence': 0,  # >= 0.7
                'low_confidence': 0  # < 0.7
            },
            'existential_quantifiers': 0,
            'universal_quantifiers': 0,
            'pattern_2_2_rules': []  # Store actual 2+2 pattern rules
        }
        
        if not rules:
            return structure_analysis
        
        for rule in rules:
            try:
                # Analyze body and head structure
                body = getattr(rule, 'body', [])
                head = getattr(rule, 'head', [])
                display = getattr(rule, 'display', str(rule))
                rule_type = getattr(rule, 'type', 'Unknown')
                accuracy = getattr(rule, 'accuracy', 0.0)
                confidence = getattr(rule, 'confidence', 0.0)
                
                body_count = len(body) if body else 0
                head_count = len(head) if head else 0
                
                # Pattern analysis
                if body_count == 1 and head_count == 1:
                    structure_analysis['pattern_analysis']['pattern_1_1'] += 1
                elif body_count == 1 and head_count == 2:
                    structure_analysis['pattern_analysis']['pattern_1_2'] += 1
                elif body_count == 2 and head_count == 1:
                    structure_analysis['pattern_analysis']['pattern_2_1'] += 1
                elif body_count == 2 and head_count == 2:
                    structure_analysis['pattern_analysis']['pattern_2_2'] += 1
                    # Store the 2+2 pattern rule for detailed analysis
                    structure_analysis['pattern_2_2_rules'].append({
                        'body': body,
                        'head': head,
                        'display': display,
                        'accuracy': accuracy,
                        'confidence': confidence,
                        'type': rule_type
                    })
                elif body_count >= 2 and head_count >= 2:
                    structure_analysis['pattern_analysis']['pattern_2_plus'] += 1
                else:
                    structure_analysis['pattern_analysis']['other_patterns'] += 1
                
                # Rule type analysis
                structure_analysis['rule_types'][rule_type] = structure_analysis['rule_types'].get(rule_type, 0) + 1
                
                # Accuracy analysis
                if accuracy == 1.0:
                    structure_analysis['accuracy_stats']['perfect_accuracy'] += 1
                elif accuracy >= 0.9:
                    structure_analysis['accuracy_stats']['high_accuracy'] += 1
                elif accuracy >= 0.7:
                    structure_analysis['accuracy_stats']['medium_accuracy'] += 1
                else:
                    structure_analysis['accuracy_stats']['low_accuracy'] += 1
                
                # Confidence analysis
                if confidence == 1.0:
                    structure_analysis['confidence_stats']['perfect_confidence'] += 1
                elif confidence >= 0.9:
                    structure_analysis['confidence_stats']['high_confidence'] += 1
                elif confidence >= 0.7:
                    structure_analysis['confidence_stats']['medium_confidence'] += 1
                else:
                    structure_analysis['confidence_stats']['low_confidence'] += 1
                
                # Quantifier analysis
                if '∃' in display:
                    structure_analysis['existential_quantifiers'] += 1
                if '∀' in display:
                    structure_analysis['universal_quantifiers'] += 1
                    
            except Exception as e:
                self.logger.warning(f"Error analyzing rule structure: {e}")
                continue
        
        return structure_analysis
    
    def log_structure_analysis(self, analysis: Dict[str, Any], database_name: str) -> None:
        """
        Log the structure analysis results.
        
        Args:
            analysis: Structure analysis results
            database_name: Name of the database being analyzed
        """
        total_rules = analysis['total_rules']
        
        if total_rules == 0:
            self.logger.info(f"No rules found for structural analysis in {database_name}")
            return
        
        self.logger.info(f"=== RULE STRUCTURE ANALYSIS for {database_name} ===")
        self.logger.info(f"Total rules analyzed: {total_rules}")
        
        # Pattern analysis
        patterns = analysis['pattern_analysis']
        self.logger.info("Pattern Distribution:")
        self.logger.info(f"  • 1 body → 1 head: {patterns['pattern_1_1']} ({patterns['pattern_1_1']/total_rules*100:.1f}%)")
        self.logger.info(f"  • 1 body → 2 head: {patterns['pattern_1_2']} ({patterns['pattern_1_2']/total_rules*100:.1f}%)")
        self.logger.info(f"  • 2 body → 1 head: {patterns['pattern_2_1']} ({patterns['pattern_2_1']/total_rules*100:.1f}%)")
        self.logger.info(f"  • 2 body → 2 head: {patterns['pattern_2_2']} ({patterns['pattern_2_2']/total_rules*100:.1f}%) [TARGET PATTERN]")
        self.logger.info(f"  • 2+ body → 2+ head: {patterns['pattern_2_plus']} ({patterns['pattern_2_plus']/total_rules*100:.1f}%)")
        self.logger.info(f"  • Other patterns: {patterns['other_patterns']} ({patterns['other_patterns']/total_rules*100:.1f}%)")
        
        # Quality analysis
        acc_stats = analysis['accuracy_stats']
        conf_stats = analysis['confidence_stats']
        self.logger.info("Quality Distribution:")
        self.logger.info(f"  • Perfect accuracy (1.0): {acc_stats['perfect_accuracy']} ({acc_stats['perfect_accuracy']/total_rules*100:.1f}%)")
        self.logger.info(f"  • High accuracy (≥0.9): {acc_stats['high_accuracy']} ({acc_stats['high_accuracy']/total_rules*100:.1f}%)")
        self.logger.info(f"  • Perfect confidence (1.0): {conf_stats['perfect_confidence']} ({conf_stats['perfect_confidence']/total_rules*100:.1f}%)")
        self.logger.info(f"  • High confidence (≥0.9): {conf_stats['high_confidence']} ({conf_stats['high_confidence']/total_rules*100:.1f}%)")
        
        # Quantifier analysis
        self.logger.info("Quantifier Distribution:")
        self.logger.info(f"  • Universal (∀): {analysis['universal_quantifiers']} ({analysis['universal_quantifiers']/total_rules*100:.1f}%)")
        self.logger.info(f"  • Existential (∃): {analysis['existential_quantifiers']} ({analysis['existential_quantifiers']/total_rules*100:.1f}%)")
        
        # Highlight 2+2 pattern rules if found
        pattern_2_2_count = patterns['pattern_2_2']
        if pattern_2_2_count > 0:
            self.logger.info(f"🎯 FOUND {pattern_2_2_count} rules with 'if a and b then c and d' pattern!")
            # Show top 3 examples
            top_2_2_rules = sorted(
                analysis['pattern_2_2_rules'], 
                key=lambda x: (x['accuracy'], x['confidence']), 
                reverse=True
            )[:3]
            
            self.logger.info("Top 3 examples of 2+2 pattern rules:")
            for i, rule in enumerate(top_2_2_rules, 1):
                self.logger.info(f"  {i}. {rule['display'][:100]}{'...' if len(rule['display']) > 100 else ''}")
                self.logger.info(f"     Accuracy: {rule['accuracy']:.3f}, Confidence: {rule['confidence']:.3f}")
        
        self.logger.info("=" * 60)

    def save_pattern_2_2_rules(self, structure_analysis: Dict[str, Any], db_results_dir: Path, db_stem: str) -> None:
        """
        Save pattern 2+2 rules to a separate JSON file for further analysis.
        
        Args:
            structure_analysis: Structure analysis results
            db_results_dir: Directory to save the file
            db_stem: Database stem name
        """
        pattern_2_2_rules = structure_analysis.get('pattern_2_2_rules', [])
        
        if pattern_2_2_rules:
            pattern_file_name = f"pattern_2_2_rules_{db_stem}.json"
            pattern_file_path = db_results_dir / pattern_file_name
            
            import json
            
            pattern_data = {
                'metadata': {
                    'database': db_stem,
                    'algorithm': self.algorithm_name,
                    'generation_date': datetime.datetime.now().isoformat(),
                    'total_pattern_2_2_rules': len(pattern_2_2_rules),
                    'description': 'Rules with exactly 2 predicates in body and 2 predicates in head'
                },
                'rules': pattern_2_2_rules,
                'statistics': {
                    'avg_accuracy': sum(r['accuracy'] for r in pattern_2_2_rules) / len(pattern_2_2_rules),
                    'avg_confidence': sum(r['confidence'] for r in pattern_2_2_rules) / len(pattern_2_2_rules),
                    'perfect_accuracy_count': len([r for r in pattern_2_2_rules if r['accuracy'] == 1.0]),
                    'perfect_confidence_count': len([r for r in pattern_2_2_rules if r['confidence'] == 1.0])
                }
            }
            
            try:
                with open(pattern_file_path, 'w', encoding='utf-8') as f:
                    json.dump(pattern_data, f, indent=2, ensure_ascii=False)
                
                self.logger.info(f"Saved {len(pattern_2_2_rules)} pattern 2+2 rules to: {pattern_file_path}")
                
            except Exception as e:
                self.logger.error(f"Error saving pattern 2+2 rules: {e}")

def setup_signal_handlers(monitor: ResourceMonitor, logger: logging.Logger) -> None:
    """
    Sets up signal handlers for graceful shutdown.
    """
    def handle_signal(signum: int, frame: Any) -> None:
        logger.info(f"Received signal {signum}. Shutting down gracefully...")
        try:
            monitor.stop()  # type: ignore
        except AttributeError:
            logger.warning("Monitor does not have a stop method")
        sys.exit(0)

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)


def main() -> None:
    """Main entry point of the script."""
    args = parse_arguments()
    config = load_config(args.config)
    mlflow_process = None  # Initialize MLflow process variable

    # Extract configuration with defaults and apply command line overrides
    monitor_config = cast(Dict[str, Any], config.get("monitor", {}))
    threshold = int(monitor_config.get("memory_threshold", 15 * 1024 * 1024 * 1024))  # 15GB
    timeout = int(monitor_config.get("timeout", 1600))  # 100 seconds

    # Database directory - command line override or config
    if args.db_dir:
        database_path = Path(args.db_dir)
    else:
        db_config = cast(Dict[str, Any], config.get("database", {}))
        db_path = str(db_config.get("path", "../data/db/"))
        database_path = Path(db_path)
    
    logging_config = cast(Dict[str, Any], config.get("logging", {}))
    log_dir_path = str(logging_config.get("log_dir", "data/logs/"))
    log_dir = Path(log_dir_path)
    
    results_config = cast(Dict[str, Any], config.get("results", {}))
    results_dir_path = str(results_config.get("output_dir", "data/results/"))
    results_dir = Path(results_dir_path)
    
    # Algorithm - command line override or config
    if args.algorithm:
        algorithm_name = args.algorithm
    else:
        algo_config = cast(Dict[str, Any], config.get("algorithm", {}))
        algorithm_name = str(algo_config.get("name", "MAHILDA"))

    # Initialize directories
    initialize_directories(results_dir, log_dir)

    # Configure logger
    logger = configure_global_logger(str(log_dir))

    # Check for foreign key errors in existing logs
    logger.info("Checking for foreign key errors in log files...")
    check_foreign_key_errors(log_dir, logger)

    # Determine MLflow usage and start server if needed
    mlflow_process = None
    use_mlflow = False
    if mlflow_available:
        mlflow_config = cast(Dict[str, Any], config.get("mlflow", {}))
        use_mlflow = bool(mlflow_config.get("use", False))
        if use_mlflow:
            logger.info("MLflow is enabled.")
            # Extract port from tracking URI if available
            tracking_uri = str(mlflow_config.get("tracking_uri", "http://localhost:5000"))
            try:
                port = int(tracking_uri.split(":")[-1])
            except (ValueError, IndexError):
                port = 5000
            
            # Start MLflow server
            mlflow_process = start_mlflow_server(port, logger)
    else:
        mlflow_config = cast(Dict[str, Any], config.get("mlflow", {}))
        if mlflow_config.get("use", False):
            logger.warning("MLflow is not available. Proceeding without MLflow.")
            use_mlflow = False

    # Find database files
    try:
        # Build effective blacklist from CLI, file, and config
        cli_blacklist = set(args.blacklist or [])
        file_blacklist = set(load_blacklist_from_file(args.blacklist_file)) if args.blacklist_file else set()
        cfg_blacklist_file = cast(Dict[str, Any], config.get('blacklist', {})).get('blacklist_file')
        cfg_blacklist_list = cast(Dict[str, Any], config.get('blacklist', {})).get('blacklist', [])
        cfg_file_blacklist = set(load_blacklist_from_file(cfg_blacklist_file)) if cfg_blacklist_file else set()
        cfg_list_blacklist = set(cfg_blacklist_list or [])

        effective_blacklist = set()
        effective_blacklist.update(cli_blacklist)
        effective_blacklist.update(file_blacklist)
        effective_blacklist.update(cfg_file_blacklist)
        effective_blacklist.update(cfg_list_blacklist)
        # Merge hard-coded blacklist
        try:
            effective_blacklist.update([s for s in HARD_CODED_BLACKLIST if s])
        except Exception:
            # Defensive: if constant is missing or malformed, ignore it
            pass

        if effective_blacklist:
            logger.info(f"Using blacklist to skip databases: {sorted(effective_blacklist)}")

        # Combine args.exclude with blacklist
        combined_exclude = set(args.exclude or []) | effective_blacklist

        db_files = find_database_files(database_path, args.include, list(combined_exclude))

        # Auto-blacklist databases that already have results unless force is set
        if not args.force:
            remaining = []
            skipped_existing = []
            for db in db_files:
                stem = db.stem
                # Expected result files
                json_name = f"{algorithm_name}_{stem}_results.json"
                report_name = f"report_{algorithm_name}_{stem}.md"
                result_path = results_dir / stem / json_name
                report_path = results_dir / stem / report_name
                legacy_json = results_dir / f"{algorithm_name}_{stem}" / json_name
                legacy_report = results_dir / f"{algorithm_name}_{stem}" / report_name
                top_level_report = results_dir / report_name

                if any(file_nonempty(p) for p in [result_path, report_path, legacy_json, legacy_report, top_level_report]):
                    skipped_existing.append(db.name)
                    continue
                remaining.append(db)

            if skipped_existing:
                logger.info(f"Skipping {len(skipped_existing)} databases because results already exist (use --force to override): {sorted(skipped_existing)}")

            db_files = remaining

        logger.info(f"Found {len(db_files)} database files to process")
        for db_file in db_files:
            logger.info(f"  - {db_file.name}")
    except (FileNotFoundError, ValueError) as e:
        logger.error(f"Error finding database files: {e}")
        sys.exit(1)

    # Initialize Resource Monitor
    monitor = ResourceMonitor(threshold, timeout)
    monitor_thread = threading.Thread(target=monitor.monitor, daemon=True)
    monitor_thread.start()
    logger.debug("Resource monitor started.")

    # Setup signal handlers for graceful shutdown
    setup_signal_handlers(monitor, logger)

    # Initialize DatabaseBatchProcessor
    processor = DatabaseBatchProcessor(
        algorithm_name=algorithm_name,
        database_path=database_path,
        results_dir=results_dir,
        logger=logger,
        use_mlflow=use_mlflow,
        config=config,
        resource_monitor=monitor,
        force_rerun=args.force,
    )

    logger.info(f"Starting batch rule discovery process with algorithm: {algorithm_name}")
    logger.info(f"Processing databases from: {database_path}")
    logger.info(f"Results will be saved to: {results_dir}")

    try:
        processor.process_all_databases(db_files, args.parallel)
        logger.info("Batch processing completed successfully.")

    except Exception as e:
        logger.error("An error occurred during the batch rule discovery process.", exc_info=True)
        sys.exit(1)
    finally:
        # Stop resource monitor if we started it
        try:
            if hasattr(monitor, 'stop'):
                monitor.stop()
                logger.info("Resource monitor stopped.")
        except Exception as e:
            logger.warning(f"Error stopping resource monitor: {e}")
        
        # Stop MLflow server if we started it
        if mlflow_process is not None:
            logger.info("Stopping MLflow server...")
            try:
                mlflow_process.terminate()
                mlflow_process.wait(timeout=10)
                logger.info("MLflow server stopped successfully")
            except subprocess.TimeoutExpired:
                logger.warning("MLflow server did not stop gracefully, killing process")
                mlflow_process.kill()
            except Exception as e:
                logger.warning(f"Error stopping MLflow server: {e}")
        
        if use_mlflow and mlflow_available and mlflow is not None:
            try:
                if mlflow.active_run():  # type: ignore
                    mlflow.end_run()  # type: ignore
                    logger.info("MLflow run ended.")
            except Exception as e:
                logger.warning(f"Error ending MLflow run: {e}")


if __name__ == "__main__":
    main()
