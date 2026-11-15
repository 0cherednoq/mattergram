# Upstream provenance

Original tooling and native mattergram code use the project MIT license.
Pinned reference snapshots are development inputs, stored outside distributions.
Their commits and every downloaded file hash are recorded in `upstream.lock.json`.

- aiogram: MIT, `LICENSES/aiogram-MIT.txt`. Source: https://github.com/aiogram/aiogram.
- aiogram_dialog: Apache-2.0, `LICENSES/aiogram-dialog-Apache-2.0.txt`. Source: https://github.com/Tishka17/aiogram_dialog. E8-01 has a reviewed development-stage selection and extraction; no dialog code is in the distributed runtime yet.
- Mattermost API sources: see the pinned `api/README.md` and `LICENSE.txt`. The API README explicitly identifies documentation as CC BY-NC-SA 3.0. These are protocol reference inputs, not MIT-licensed code to redistribute.
- Mattermost developer documentation: see the pinned `LICENSE.txt`. UI references are retained in development snapshots, not copied into the runtime.

Do not bundle the donor trees, Mattermost schema descriptions, or reference prose
into wheel/sdist. Before E7 generation, resolve what may be generated and distributed
from each source; keep endpoint semantics separate from copied documentation text.

Each extracted file must have a manifest entry with its donor path/hash, the
license, codemods, adaptation reason and related test provenance. An extraction
receipt records output hashes. Inclusion of a license here does not mean that an
entire upstream package has been ported.
