import yaml
from collections import OrderedDict
import os

class ConfigManager:
    def __init__(self, config_file, default_config=None):
        """
        Initializes the ConfigManager.

        :param config_file: Path to the YAML config file.
        :param default_config: A dictionary of default values for specific settings.
        """
        self.config_file = config_file
        self.default_config = default_config or OrderedDict()

        # Load the config file or create it if it doesn't exist
        self.config = self._load_config()

    def _load_config(self):
        """Load the config from the YAML file, applying defaults if necessary."""
        if os.path.exists(self.config_file):
            with open(self.config_file, 'r') as file:
                config = yaml.load(file, Loader=self._get_ordered_loader()) or OrderedDict()
        else:
            config = OrderedDict()

        # Merge the loaded config with the default config, preserving root-level order
        merged_config = OrderedDict(self.default_config)
        merged_config.update(config)
        return merged_config

    def _get_ordered_loader(self):
        """Custom YAML loader to load root-level mappings as OrderedDict."""
        class OrderedLoader(yaml.SafeLoader):
            pass

        def construct_mapping(loader, node):
            loader.flatten_mapping(node)
            return OrderedDict(loader.construct_pairs(node))

        OrderedLoader.add_constructor(
            yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
            construct_mapping)
        return OrderedLoader

    def _get_ordered_dumper(self):
        """Custom YAML dumper to dump OrderedDicts at the root level."""
        class OrderedDumper(yaml.SafeDumper):
            pass

        def _dict_representer(dumper, data):
            return dumper.represent_dict(data.items())

        OrderedDumper.add_representer(OrderedDict, _dict_representer)
        return OrderedDumper

    def get(self, key, default=None):
        """
        Retrieve a configuration value by key. If the key doesn't exist,
        check for a default value, otherwise return None or the provided default.

        :param key: The configuration key to retrieve.
        :param default: A fallback default value if the key is not found.
        :return: The value from the config or the default.
        """
        return self.config.get(key, default)

    def set(self, key, value):
        """
        Set a configuration value by key.

        :param key: The configuration key.
        :param value: The value to set.
        """
        self.config[key] = value

    def save(self):
        """Save the current configuration back to the YAML file, preserving root-level order."""
        with open(self.config_file, 'w') as file:
            yaml.dump(self.config, file, Dumper=self._get_ordered_dumper(), default_flow_style=False)

    def get_all(self):
        """
        Retrieve the entire configuration dictionary.

        :return: The configuration dictionary.
        """
        return self.config
