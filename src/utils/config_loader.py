import yaml
import logging

def load_config(config_path: str) -> dict:
    """Loads configuration from a YAML file."""
    logger = logging.getLogger(__name__)
    try:
        with open(config_path, 'r') as file:
            return yaml.safe_load(file)
    except FileNotFoundError:
        logger.error(f"Configuration file not found: {config_path}")
        return {}
    except yaml.YAMLError as e:
        raise ValueError(f"Error parsing configuration file: {e}")