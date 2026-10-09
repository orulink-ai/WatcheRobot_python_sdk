"""Bundle only explicitly catalogued demo resources; never local runtime data."""

import json
from pathlib import Path

from hatchling.builders.hooks.plugin.interface import BuildHookInterface


class CustomBuildHook(BuildHookInterface):
    def initialize(self, version, build_data):
        if self.target_name != 'wheel':
            return
        root = Path(self.root)
        catalog = json.loads((root / 'src/watcherobot/bundled-apps.json').read_text(encoding='utf-8'))
        for spec in catalog.values():
            directory = spec['directory']
            for relative in spec['files']:
                source = root / 'examples' / directory / relative
                if not source.is_file() or source.is_symlink():
                    raise ValueError(f'Missing or unsafe bundled demo resource: {source}')
                build_data['force_include'][str(source)] = (
                    f'watcherobot/_bundled_apps/{directory}/{relative}'
                )
