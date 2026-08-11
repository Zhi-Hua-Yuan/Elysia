For full documentation, please visit our [documentation site](https://open-llm-vtuber.github.io/) or view the [source repository](https://github.com/Open-LLM-VTuber/open-llm-vtuber.github.io).

> **Note:**  
> The `sample_conf` directory contains legacy sample configuration files for running various models with sherpa-onnx. These files are deprecated and will be removed after we extract the relevant sherpa-onnx information.

## MVP baseline notes

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
