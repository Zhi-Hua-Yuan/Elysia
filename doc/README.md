For full documentation, please visit our [documentation site](https://open-llm-vtuber.github.io/) or view the [source repository](https://github.com/Open-LLM-VTuber/open-llm-vtuber.github.io).

> **Note:**  
> The `sample_conf` directory contains legacy sample configuration files for running various models with sherpa-onnx. These files are deprecated and will be removed after we extract the relevant sherpa-onnx information.

## MVP baseline notes

- The completed M5.2 local storage and dynamic context injection acceptance is
  maintained in
  [`elysia-m5-2-acceptance.md`](elysia-m5-2-acceptance.md).

- The frozen M5 lightweight persistent-memory design is maintained in
  [`elysia-m5-lightweight-persistent-memory-design.md`](elysia-m5-lightweight-persistent-memory-design.md).
- M5.4.3 memory management UI usage, troubleshooting, automated evidence, and
  pending manual checks are tracked in
  [`elysia-m5-4-3-memory-ui.md`](elysia-m5-4-3-memory-ui.md).

- The current M3.4 Fish-default software RC setup, smoke test, fallback, and
  troubleshooting guide is maintained in
  [`elysia-software-rc-runbook.md`](elysia-software-rc-runbook.md).

- The completed M3.4 reproducibility and software RC acceptance record is
  maintained in
  [`elysia-m3-4-acceptance.md`](elysia-m3-4-acceptance.md).

- The completed M2 stabilization and reproducibility acceptance record is
  maintained in [`elysia-m2-acceptance.md`](elysia-m2-acceptance.md).

- The M3 asset intake and rights boundary is maintained in
  [`elysia-m3-asset-contract.md`](elysia-m3-asset-contract.md).

- The Fish Audio online voice technical acceptance and measured latency are
  maintained in
  [`elysia-m3-fish-tts-acceptance.md`](elysia-m3-fish-tts-acceptance.md).

- The M3.3 Fish-default software baseline and pending manual checklist are
  maintained in
  [`elysia-m3-3-acceptance.md`](elysia-m3-3-acceptance.md).

- The current Elysia project status and milestone plan is maintained in
  [`elysia-project-progress.md`](elysia-project-progress.md).

- The reproducible Windows/uv setup guide for MVP-A is maintained in
  [`elysia-mvp-a-setup.md`](elysia-mvp-a-setup.md).

- The M2.3 reproducible setup acceptance record is maintained in
  [`elysia-m2-3-acceptance.md`](elysia-m2-3-acceptance.md).

- The MVP-A smoke checklist and symptom-based troubleshooting guide is
  maintained in
  [`elysia-mvp-a-smoke-and-troubleshooting.md`](elysia-mvp-a-smoke-and-troubleshooting.md).

- The M2.4 smoke-test acceptance record is maintained in
  [`elysia-m2-4-acceptance.md`](elysia-m2-4-acceptance.md).

- The frozen backend, Web frontend, and Electron runtime relationship is
  recorded in [`elysia-m2-runtime-baseline.md`](elysia-m2-runtime-baseline.md).

- The Elysia M1 Persona v1 acceptance record is maintained in
  [`elysia-m1-acceptance.md`](elysia-m1-acceptance.md).

- The Elysia M0 functional and performance acceptance record is maintained in
  [`elysia-m0-acceptance.md`](elysia-m0-acceptance.md).

- `edge-tts` uses Microsoft Edge's **online** text-to-speech service. It does
  not require an API key, but it does require network access and must not be
  described as a local or offline TTS engine.
- The backend repository is licensed under the MIT License. The separately
  maintained Web/Electron frontend uses the `Open-LLM-VTuber License 1.0`
  starting with frontend v1.2.0. Bundled Live2D sample assets are governed by
  their own terms; review all three license scopes before redistribution.
- YAML configuration values support `${ENV_VAR}` substitution through
  `config_manager.utils.read_yaml`. API keys may therefore be supplied through
  process environment variables, for example
  `llm_api_key: '${OPEN_LLM_VTUBER_API_KEY}'`. If the variable is absent, the
  placeholder remains unchanged; a local `conf.yaml` is gitignored and remains
  the supported fallback for local credentials.
