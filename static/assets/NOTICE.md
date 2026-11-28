# Optional local artwork

No game artwork is distributed with this plugin. The UI works without images,
using CSS gradients and character names; motion respects reduced-motion settings.

Users may run `tools/export_ui_art.py <path-to-local-game.pck>` to extract art
from their own installation into `static/local-art/`. This directory is ignored
by Git and excluded from plugin packages. `sources.json` is generated locally.
The extractor does not download or modify the game. Game art belongs to Mega Crit
and its respective owners, not the source-code license of this repository.

Earlier commits contained extracted images. Removing them from the current tree
does not erase historical Git objects; this change does not rewrite public history.
