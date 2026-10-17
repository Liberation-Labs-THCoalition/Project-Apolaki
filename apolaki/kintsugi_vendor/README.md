# Vendored Kintsugi core (Apolaki)

Verbatim copies of the Kintsugi modules Apolaki's engine wiring needs (BDI, skill base, DAG, registry, router), from
[Liberation-Labs-THCoalition/Project-Kintsugi](https://github.com/Liberation-Labs-THCoalition/Project-Kintsugi) at
commit `8e4fae5629074753abc2c1457d2cf705da015695` (2026-10-16). Their whole import closure, walked with an AST including package `__init__`
files: 11 modules, standard library only.

| File | Modified? |
|---|---|
| `kintsugi/__init__.py` | verbatim |
| `kintsugi/bdi/__init__.py` | verbatim |
| `kintsugi/bdi/coherence.py` | verbatim |
| `kintsugi/bdi/drift_classifier.py` | verbatim |
| `kintsugi/bdi/models.py` | verbatim |
| `kintsugi/bdi/store.py` | verbatim |
| `kintsugi/skills/__init__.py` | verbatim |
| `kintsugi/skills/base.py` | verbatim |
| `kintsugi/skills/dag.py` | verbatim |
| `kintsugi/skills/registry.py` | verbatim |
| `kintsugi/skills/router.py` | verbatim |

`apolaki/kintsugi_core.py` loads the real engine first (`KINTSUGI_PATH`, then an installed `kintsugi`) and this copy
last, and records which one it used in `KINTSUGI_SOURCE`.

**Licence.** These files are Project-Kintsugi's and stay under its terms: its `LICENSE.md` (Hippocratic License 3.0,
Liberation Labs) and `LICENSE-COMMERCIAL-ADDENDUM.md`, the same terms as this repository.

These are verbatim. To update, re-copy from Kintsugi's main and change the commit above.
