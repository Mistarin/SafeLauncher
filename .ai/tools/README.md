# AI Cache Tools

Run these from the repository root:

```bash
python .ai/tools/build_manifest.py
python .ai/tools/validate_cache.py
```

`build_manifest.py` produces disposable JSON indexes under `.ai/generated/` and `.ai/manifest.json`. It uses only the Python standard library, AST parsing for Python, and conservative text extraction for TypeScript, SQL, shell, and Qt connection points.

`validate_cache.py` checks cache links, source references, metadata, forbidden runtime/secrets content, and generated output presence. It does not contact network services or execute the launcher.
