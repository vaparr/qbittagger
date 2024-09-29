import yaml
import os

class ConfigManager:
    def __init__(self, config_file, default_config=None):
        """
        Initializes the ConfigManager.

        :param config_file: Path to the YAML config file.
        :param default_config: A dictionary of default values for specific settings.
        """
        self.config_file = config_file
        self.default_config = default_config or {}

        # Load the config file or create it if it doesn't exist
        self.config = self._load_config()

    def _load_config(self):
        """Loads the config from the YAML file, applying defaults if necessary."""
        if os.path.exists(self.config_file):
            with open(self.config_file, 'r') as file:
                config = yaml.safe_load(file) or {}
        else:
            config = {}

        # Merge the loaded config with the default config
        return {**self.default_config, **config}

    def get(self, key, default=None):
        """
        Retrieve a configuration value by key. If the key doesn't exist, 
        check for a default value, otherwise return None or the provided default.

        :param key: The configuration key to retrieve.
        :param default: A fallback default value if the key is not found.
        :return: The value from the config or the default.
        """
        # First, check if the key exists in the current configuration
        if key in self.config:
            return self.config[key]
        
        # Next, check if there is a per-key default in the default_config dictionary
        if key in self.default_config:
            return self.default_config[key]
        
        # Finally, return the provided default value or None
        return default

    def set(self, key, value):
        """
        Set a configuration value by key.

        :param key: The configuration key.
        :param value: The value to set.
        """
        self.config[key] = value

    def save(self):
        """Saves the current configuration back to the YAML file."""
        with open(self.config_file, 'w') as file:
            yaml.dump(self.config, file)

    def get_all(self):
        """
        Retrieve the entire configuration dictionary.

        :return: The configuration dictionary.
        """
        return self.config
