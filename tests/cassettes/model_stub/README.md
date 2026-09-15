# Model stub cassettes

Cassettes served by the local stack's model stub (`tools/model_stub`) for any
model id that is not `scripted/<name>`. They use the same key and file format
as `core/model_replay.py`, computed from the OpenAI-style request the stub
receives, so they are separate from the per-test cassettes next to this
directory.

Record deliberately (this calls a real model and needs a key):

```bash
MODEL_STUB_MODE=record MODEL_RECORD_API_KEY=... make dev
```

then review and commit the new `*.json` files like any other fixture. See
"Model stub" in `docs/quickstart-local.md`.
