# bookworm

A FastAPI service that turns a text prompt into a print-ready book cover in the
trade formats German literary publishers actually use.

```
POST /generate  { text, title, author }
   -> Claude writes the art direction and the image prompt
   -> OpenAI paints the artwork
   -> the typography engine sets the type in real trade geometry
   -> front.png / front.jpg / front.svg / spread.pdf
```

The division of labour is deliberate. Image models cannot set type — generated
lettering is malformed, unlicensed and unprintable — so they never see the title.
The image model paints artwork; the title, author, Gattungsbezeichnung, spine and
back-cover copy are set afterwards as outlined vector type at the exact trim size.

## Layout

`bookworm` is the project. **`covers` is the package** that generates covers — named
after its endpoint, because it is on its way to being one component of a larger
book-metadata API rather than a service of its own. Everything cover-related is
`src/covers/`; nothing in it may configure the process (no `.env` loading, no root
logger, no middleware) — that belongs to whatever application composes it.

Settings are prefixed **`COVERS_*`** so they can share one `.env` with the rest of that
API. They were `BOOKWORM_*`; rename them in your `.env` if you set any. Provider keys
(`ANTHROPIC_API_KEY`, `OPENAI_API_KEY`) keep their conventional names.

## Quick start

```bash
brew install cairo          # Debian: apt-get install libcairo2
uv sync --extra dev
cp .env.example .env        # add your keys; it runs without them, see below
uv run uvicorn bookworm.main:app --reload   # books + covers
```

```bash
curl -X POST localhost:8000/generate -H 'content-type: application/json' -d '{
  "text": "Eine Übersetzerin kehrt in die Stadt ihrer Kindheit zurück und findet dort nur noch die Sprache wieder, in der sie nie gelebt hat.",
  "title": "Die zweite Sprache",
  "author": "Helena Marr",
  "format": "kiwi_klappenbroschur",
  "pages": 336,
  "isbn": "978-3-462-00123-4",
  "price": "€ 24,00 [D]"
}'
```

The response carries the art direction, the full print geometry, and URLs for each
asset. `?inline=front_png` returns the image directly instead of JSON.

Generate covers from the sample EPUBs, reading title, author and page count out of
the container:

```bash
uv run scripts/from_epub.py examples/*.epub --local
```

## It runs without either key

Both model steps degrade rather than fail, so the service is useful offline and a
missing key never turns into a 500:

| Missing | What happens |
|---|---|
| `ANTHROPIC_API_KEY` | A deterministic brief is derived from a hash of the input: palette, layout, typeface and genre line are chosen from the built-in catalogue, and the blurb is cut from the input text. `art_direction_meta.source` reports `fallback`. |
| `OPENAI_API_KEY` | The cover renders with a procedural vector motif instead of painted artwork, and says so in `notes`. |

Everything else — geometry, typesetting, rasterising — is local and deterministic.

Degrading quietly is convenient but easy to misread: a cover with a flat vector
motif and no picture usually means a key did not resolve, not that the art
direction chose austerity. Check `art_direction_meta.source` and `notes`, or call
`GET /healthz`, which reports whether each key is visible.

`load_dotenv()` resolves `.env` relative to the package, so it is found when the
service runs from the project. A script living elsewhere has to pass the path.

## Books

`books` is the catalogue package: CRUD over a SQLite database, served next to the
cover endpoints by `bookworm.main:app`. (`covers.main:app` still runs covers alone.)

| Method | Path | |
|---|---|---|
| `GET` | `/books` | Filter with `publisher`, `category` and `format`, and search title, author and subtitle with `q`. Paginate with `limit` (default 50, max 200) and `offset`. Returns `{items, total, limit, offset}`. |
| `GET` | `/books/{slug}` | One book, or 404 |
| `POST` | `/books` | 201; 409 if the slug or ISBN is taken |
| `PUT` | `/books/{slug}` | Full replace, generated covers included; the slug may change |
| `DELETE` | `/books/{slug}` | 204 |

The database lives at `BOOKS_DB_PATH` (default `data/books.db`). On first start it is
created and seeded from `src/books/seed.json`, which is an export of
`../recover/books.ts` (`node scripts/export_books_ts.mjs`). After that the database
is the source of truth, and re-exporting does not change a database that already has books.

## Formats

`GET /catalogue` lists them with computed spine widths. These are the customary
trade formats associated with each imprint, taken from general book-trade practice
rather than from a publisher's production spec sheet. **Reconcile against the
`Umschlagvorgabe` the publisher's production department sends you before going to
press.**

| Key | Format | Trim (mm) | Binding |
|---|---|---|---|
| `rororo_taschenbuch` | rororo Taschenbuch | 118 × 190 | Taschenbuch |
| `rowohlt_paperback` | Paperback | 135 × 205 | Paperback |
| `rowohlt_hardcover` | Hardcover mit Schutzumschlag | 140 × 215 | Hardcover, 85 mm Klappen |
| `kiwi_paperback` | KiWi-Paperback | 125 × 190 | Paperback |
| `kiwi_taschenbuch` | KiWi-Taschenbuch | 125 × 200 | Taschenbuch |
| `kiwi_klappenbroschur` | Klappenbroschur | 135 × 215 | Klappenbroschur, 90 mm Klappen |
| `kiwi_hardcover` | Hardcover Leinen mit Schutzumschlag | 135 × 210 | Hardcover, 85 mm Klappen |
| `suhrkamp_taschenbuch` | suhrkamp taschenbuch | 108 × 177 | Taschenbuch |
| `din_a5_hardcover` | Hardcover DIN A5 | 148 × 210 | Hardcover, 85 mm Klappen |
| `grossformat_hardcover` | Großformatiges Hardcover | 155 × 230 | Hardcover, 95 mm Klappen |

What the geometry accounts for:

- **Beschnittzugabe** 3 mm on every outer edge.
- **Sicherheitsabstand** 5 mm — live type is kept inside it.
- **Rückenstärke** from the page count: `Bogen × (g/m² × Volumen ÷ 1000)`. A
  hardcover jacket additionally clears both boards plus a rounding allowance.
  Below 7 mm the spine is left as flat colour and `notes` says why.
- **Überstand** 3 mm on hardcovers, so the jacket is sized to the case rather
  than the book block.
- **Klappen** on flapped formats, laid out as `back flap | back | spine | front | front flap`.
- A reserved white **EAN field** (45 × 30 mm) on the back cover. It is reserved,
  not drawn — the printer drops in the real barcode film.
- `marks: true` adds trim and fold lines for proofing.

## Output

| Asset | What it is |
|---|---|
| `front_svg` / `spread_svg` | Vector master. Type is already outlined. |
| `front_pdf` / `spread_pdf` | Vector PDF at exact physical page size, **no embedded fonts** — nothing to license or substitute. |
| `front_png` / `spread_png` | 300 dpi by default, with the resolution stamped in the file. |
| `front_jpg` | sRGB JPEG for catalogue and web use. |
| `direction_json` | The brief, geometry and seed, for reproducing the cover. |

PDFs are RGB. A repro house that needs CMYK with a specific ICC profile should
convert the vector PDF; converting here would mean guessing the press profile.

## Typography

Type is shaped with HarfBuzz and emitted as outlined glyph paths, so one set of
shaped advances drives the line fitting, the SVG, the PNG and the PDF — there is
no second text engine to disagree with the first, and the output has no font
dependency.

- Display titles are fitted by trying every line count up to four and keeping
  whichever gives the largest type, with the line breaks balanced to even out the
  rag.
- Type is positioned off the **cap line**, not the baseline, which is what the eye
  aligns to at display sizes.
- Capital umlauts open the leading automatically. `SCHULD / SÜHNE` set at 0.88
  leading collides; German titles are full of Ä, Ö and Ü.
- Display sizes get slightly negative tracking; small labels get positive.
- `ß` uppercases to `SS`, per German typographic practice.

Faces are the macOS system fonts, grouped into logical families
(`geometric` is Futura, `neoclassical` is Didot, `grotesk` is Helvetica Neue, and
so on) with a fallback chain. Drop TTF/OTF files into `src/covers/fonts/` to
override. **Check the licence before printing commercially** — the bundled macOS
faces are licensed for use on the machine, not for redistribution.

## Layouts

### Style registers

`style` picks the register the cover is briefed in. It conditions the system prompt,
the fallback brief, the template shortlist and how tightly the artwork is held to
the palette. There is no single German house style, so this is the caller's choice
rather than a default baked into the prompt.

| `style` | What you get |
|---|---|
| `illustrated` *(default)* | A drawn, figurative picture carrying the cover — gouache, coloured pencil, ink and wash. Specific subjects and telling details, generous hand-mixed colour. `illustrated_full` layout. |
| `painterly` | A painting: oil or gouache, real brushwork, atmosphere over outline. Tonal, full colour range. |
| `typographic` | Type-led and austere. Flat grounds, two or three colours, imagery abstract or absent. |

### Templates

| Template | Idiom |
|---|---|
| `illustrated_full` | The picture runs across the whole cover; the type sits in a panel over it. The layout for an illustrated cover. |
| `photo_duotone` | Artwork across the upper two thirds, type below. |
| `kiwi_flat` | Flat ground, left-aligned type stack in the upper half, artwork below. |
| `rororo_band` | Full-bleed ground with a horizontal band carrying the title. |
| `type_block` | Display type filling the cover edge to edge, each line set to its own size. No imagery. |
| `didone_centre` | Centred neoclassical setting between hairline rules. |

The type panel in `illustrated_full` exists because the image model will not
reliably leave a clear corner for the title, and asking it to produces worse
pictures. A panel over a full-bleed illustration is both legible and idiomatic.

Any part of the brief can be pinned: `template`, `type_family`, `palette`,
`artwork`, `motif`, `genre_line`, `blurb`, `seed`. Pinning the template moves the
typeface with it unless you pin that too. Pinning a `motif` implies you want it
drawn.

Sixteen palettes ship in the flat, slightly austere register the idiom lives in —
`rororo_rot`, `kobalt`, `schwefel`, `pergament`, `graphit` and so on. The art
direction model may also return its own hex values.

`spine_direction` defaults to `top_to_bottom`, which is what most contemporary
German trade books do; `bottom_to_top` gives the older continental convention.

## Speed, and where it actually goes

Measured on one cover (`kiwi_klappenbroschur`, 304 pp, `illustrated`, same text):

| Configuration | Wall clock |
|---|---|
| `director="claude"`, `image_quality="high"` | 130 s |
| `director="openai"`, `image_quality="high"` | 149 s |
| `director="none"`, `image_quality="high"` | 117 s |
| `director="none"`, `image_quality="medium"` | **45 s** |
| `director="none"`, `image_quality="low"` | **21 s** |

The brief is ~13–16 s of that, whichever provider writes it (Claude 15.8 s,
`gpt-5.4` 13.2 s, measured in isolation). So switching providers buys nothing on
latency, and dropping the brief entirely buys 10%. **`image_quality` is the lever**:
high → low is 5.5× faster. The `openai + high` row came in slower than
`claude + high` purely from run-to-run image-generation variance, which is larger
than the whole brief step — don't read a provider difference into it.

`low` quality still produces a usable illustrated cover. Judge it on your own books
before committing: it is the one setting here that trades output quality for time.

## Directors

`director` chooses who writes the brief. All three produce the same
`ArtDirection` shape, so the renderer cannot tell them apart.

| `director` | Behaviour |
|---|---|
| `claude` *(default)* | `claude-opus-5`. Set `COVERS_CLAUDE_MODEL` to change. |
| `openai` | `gpt-5.4` via `responses.parse`. Set `COVERS_OPENAI_TEXT_MODEL`. One provider, one key, one bill. |
| `none` | No text model at all. The image prompt is composed locally from your text behind a register preamble; palette, layout, genre line and back-cover copy come from the deterministic brief. |

`director="none"` gives up real things: the palette is picked by hash rather than
chosen for the book, the back-cover copy is cut from your input rather than written,
and the Gattungsbezeichnung is a keyword guess. Worth it for drafts and bulk runs,
not for a cover going to press.

## Models

Set via env: `COVERS_CLAUDE_MODEL` (default `claude-opus-5`),
`COVERS_OPENAI_TEXT_MODEL` (default `gpt-5.4`) and
`COVERS_IMAGE_MODEL` (default `gpt-image-2`; the installed SDK also accepts
`gpt-image-2.5-sunburst`, `gpt-image-2.5-flare`, `gpt-image-1.5`, `gpt-image-1`).
Artwork is generated once, for the front panel, and cover-cropped to the planned
placement. The response reports the artwork's `effective_dpi` over that placement
and flags it in `notes` when it had to be resampled up, so an upscale is never
passed off as native detail. At 1024 px native that is around 190–260 dpi
depending on the placement, so artwork is the soft part of an otherwise 300 dpi
cover; the type stays vector regardless.

`treatment` defaults to `none`, so artwork keeps the colour it was painted in.
`duotone` maps luminance onto the two ends of the palette: it makes image and type
read as one system, but it discards *all* hue, including any accent the art
direction deliberately asked for. It is a strong effect, worth choosing on purpose
and not worth having as a default.

Cohesion without duotone comes from the prompt instead — the palette is passed to
the image model, loosely for `illustrated` ("let these colours lead, mix freely
around them") and strictly for `typographic` ("restrict the palette to these").

## Tests

```bash
uv run pytest
```

48 tests, ~2 seconds, no network and no credentials. `tests/conftest.py` strips
`ANTHROPIC_API_KEY` / `OPENAI_API_KEY` from the environment for every test, because
`covers.main` loads `.env` at import — without that the suite makes live, billed
image-generation calls. Tests that exercise a provider path stub the client and set
their own key.

Both live paths have been exercised end to end once: `claude-opus-5` for the brief
and `gpt-image-2` for the artwork, ~165 s wall clock for one cover.
