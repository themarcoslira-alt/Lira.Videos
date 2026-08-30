# Lira Studio — Ciclo Narrativo v0.3.5+

## Visão geral

O pipeline de produção de vídeo agora gera as cenas seguindo um **Ciclo Narrativo**
Avatar → Imagem → Vídeo (1→2→3→1→2→3...), substituindo a atribuição aleatória de
tipo de mídia por um padrão determinístico e auditável.

## Campos novos por cena (lira_scene_plan.json)

| Campo | Tipo | Valores | Descrição |
|-------|------|---------|-----------|
| `tipo_cena` | str | `avatar_intro` / `imagem_zoom` / `video_acao` | Papel da cena no ciclo |
| `efeito` | str | `zoom_in` / `fade` / `pan` / `none` | Efeito de câmera/movimento |
| `posicao_ciclo` | int | `1` / `2` / `3` | Posição no ciclo (1=avatar, 2=imagem, 3=video) |
| `duracao_planejada` | float | segundos | Duração planejada (7s / 12s / 8s) |
| `ciclo_numero` | int | `1..N` | Qual ciclo a cena pertence |

### Exemplo

```json
{
  "id": 1,
  "scene_index": 1,
  "texto": "Apresentador (@presenter) fala sobre: cuidados com a rosa",
  "tipo_cena": "avatar_intro",
  "efeito": "none",
  "posicao_ciclo": 1,
  "duracao_planejada": 7.0,
  "ciclo_numero": 1,
  "media_type": "video"
}
```

## Garantias do ciclo

- `posicao_ciclo` segue estritamente `1→2→3→1→2→3...` (mod 3);
- **nunca** há 2 `avatar_intro` consecutivos;
- `tipo_cena` nunca é nulo;
- `efeito` sempre ∈ `{zoom_in, fade, pan, none}`.

Validação em `services/scene_schema.py` (`validar_ciclo`).

## Arquivos

| Arquivo | Papel |
|---------|-------|
| `services/scene_schema.py` | Schema + validação do ciclo (aditivo) |
| `services/storyboard_builder.py` | `gerar_ciclos_narrativos()` + passada `_aplicar_ciclo_narrativo()` |
| `services/prompt_builder_service.py` | `construir_prompt_por_tipo()` — prompts por tipo_cena |
| `services/media_manager.py` | Salva mídia com nome canônico + metadata |
| `services/capcut_validator.py` | Validação pré-CapCut (3 fontes + ciclo) |
| `config.py` | `NARRATIVE_CYCLE_ENABLED`, `CYCLE_TIPOS`, durações, `FILENAME_PATTERN`, `VALID_EFEITOS` |
| `tests/test_narrative_cycle.py` | 12 testes do ciclo |

## Configuração (config.py)

```python
NARRATIVE_CYCLE_ENABLED = True
CYCLE_TIPOS = ["avatar_intro", "imagem_zoom", "video_acao"]
CYCLE_DURACAO_AVATAR = 7    # segundos
CYCLE_DURACAO_IMAGEM = 12   # segundos
CYCLE_DURACAO_VIDEO = 8     # segundos
```

## Nomenclatura de mídia

`{id:02d}_[{MM:SS}-{MM:SS}].{ext}` — físico usa `:` → `-` (Windows):
`01_[00-00-00-05].png` (display: `01_[00:00-00:05].png`).

- PNG/MP4 → `<projeto>/imagens/` ou `<projeto>/videos/`
- Metadata → `<projeto>/metadata/cena_XXX/` (`prompt.txt` + `status.json`)
- 3 fontes sincronizadas: `lira_scene_plan.json.arquivo_midia`,
  `draft_content.json.path`, arquivo físico.

## Testes

```bash
python -m unittest tests.test_narrative_cycle -v
```

Resultado: 12/12 OK (2026). Regressão dos módulos vizinhos mantida verde
(`test_storyboard_builder`, `test_scene_plan_schema`, `test_media_naming_and_resolver`).
