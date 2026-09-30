# bookworm

A FastAPI service that turns a text prompt into a print-ready front cover in the
trade formats German literary publishers actually use.

```
POST /generate  { text, title, author }
   -> Claude writes the art direction and the image prompt
   -> OpenAI paints the artwork
   -> the typography engine sets the type in real trade geometry
   -> front.png
```

The division of labour is deliberate. Image models cannot set type — generated
lettering is malformed, unlicensed and unprintable — so they never see the title.
The image model paints artwork; the title, author, Gattungsbezeichnung and imprint
are set afterwards as outlined vector type at the exact trim size.

## Layout

`bookworm` is the project. **`covers` is the package** that generates covers — named
after its endpoint, because it is on its way to being one component of a larger
book-metadata API rather than a service of its own. Everything cover-related is
`src/covers/`; nothing in it may configure the process (no `.env` loading, no root
logger, no middleware) — that belongs to whatever application composes it. Its
endpoints are `covers.api.router`; `covers.main:app` is the one module that sets up
logging and CORS, for running covers on its own. `bookworm.main:app` includes the
router alongside `books` and the book covers.

Settings are prefixed **`COVERS_*`** so they can share one `.env` with the rest of that
API, and are read when used (`covers/settings.py`), not at import, so the order of
`.env` loading and imports does not matter. They were `BOOKWORM_*`; rename them in your `.env` if you set any. Provider keys
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
  "format": "kiwi_klappenbroschur"
}'
```

The response carries the art direction, the print geometry, and `image`, the URL
of the front PNG. `?inline=true` returns the PNG directly instead of JSON.

Generate covers from the sample EPUBs, reading title, author and opening text out
of the container:

```bash
uv run scripts/from_epub.py examples/*.epub --local
```

## It runs without either key

Both model steps degrade rather than fail, so the service is useful offline and a
missing key never turns into a 500:

| Missing | What happens |
|---|---|
| `ANTHROPIC_API_KEY` | A deterministic brief is derived from a hash of the input: palette, layout, typeface and genre line are chosen from the built-in catalogue. `art_direction_meta.source` reports `fallback`. |
| `OPENAI_API_KEY` | The cover renders with a procedural vector motif instead of painted artwork, and says so in `notes`. |

Everything else — geometry, typesetting, rasterising — is local and deterministic.

Degrading quietly is convenient but easy to misread: a cover with a flat vector
motif and no picture usually means a key did not resolve, not that the art
direction chose austerity. Check `art_direction_meta.source` and `notes`, or call
`GET /healthz`, which reports whether each key is visible.

`bookworm.main` loads `.env` from the project root, so it is found whatever the
working directory. `covers.main:app` on its own loads none: export the variables,
or run the combined app. `scripts/from_epub.py --local` loads it itself.

## Books

`books` is the catalogue package: CRUD over a MongoDB collection (Atlas works),
served next to the cover endpoints by `bookworm.main:app`. (`covers.main:app` still
runs covers alone, without MongoDB.)

| Method | Path | |
|---|---|---|
| `GET` | `/books` | Filter with `publisher`, `category` and `format`, and search title, author and subtitle with `q`. Paginate with `limit` (default 50, max 200) and `offset`. Returns `{items, total, limit, offset}`. |
| `GET` | `/books/{slug}` | One book, or 404 |
| `POST` | `/books` | 201; 409 if the slug or ISBN is taken |
| `PUT` | `/books/{slug}` | Full replace, except `generated_covers`; the slug may change |
| `DELETE` | `/books/{slug}` | 204 |

Set `MONGODB_URI`, plus `MONGODB_USERNAME` and `MONGODB_PASSWORD` if the credentials
are not in the URI. The database is `MONGODB_DB` (default `bookworm`), the collection
`books`. Each book is one document, with unique indexes on `slug` and `isbn`. Its
`generated_covers` are the short entries of its published covers (see below); book
writes never set or clear them.

On start the API creates the indexes, and seeds an empty collection from
`src/books/seed.json`. After that the collection is the source of truth; editing
the seed does not touch a collection that already has books.

Tests run against `mongomock` in memory and never reach a real cluster.

## Book covers

Covers made for a book live in their own `covers` collection, with their PNGs in
GridFS (`cover_images`) in the same database, so they outlive a deploy.

Nothing about a book cover is picked by hand. A request carries only the reader
`type`, and optionally `text` to rewrite the brief; everything else follows from
three sources:

| Decided by | What |
|---|---|
| The **reader type** (`covers/moods.py`) | Register, palette leaning, the layouts and type families the art director may choose from |
| The **publisher** (`bookworm/houses.py`) | Trim format per binding, imprint wordmark (e.g. rororo for Rowohlt paperbacks) |
| The **book** | Title, author, the blurb as the brief, `category` as the Gattungsbezeichnung |

| Type | Register | Layouts | Type families |
|---|---|---|---|
| `heart` | illustrated | illustrated_full, photo_duotone | humanist, literary_serif, garalde, neoclassical |
| `suspense` | painterly | photo_duotone, rororo_band, illustrated_full | grotesk, grotesk_condensed, geometric, slab |
| `trend` | illustrated | illustrated_full, kiwi_flat, type_block | meta, geometric, grotesk_condensed, neoclassical |
| `discourse` | typographic | type_block, kiwi_flat, didone_centre | garalde, literary_serif, didone, grotesk, meta |

The art director still chooses per book, inside those limits: the model is asked
for a brief whose schema only admits the type's layouts and families, so it
cannot stray, and the no-model fallback picks from the same lists. Who writes the brief, image
quality and resolution are service settings (`COVERS_DIRECTOR`,
`COVERS_IMAGE_QUALITY`), not per-cover choices. `/generate` takes the same `mood`
for covers made outside a book, alongside its full set of overrides.

| Method | Path | |
|---|---|---|
| `POST` | `/books/{slug}/covers` | Generate a draft. 201 |
| `GET` | `/books/{slug}/covers` | Newest first; filter with `status` and `type` |
| `GET` | `/books/{slug}/covers/{id}` | The full record: options, effective request, brief, geometry, notes |
| `PATCH` | `/books/{slug}/covers/{id}` | `color`, `theme`, without regenerating |
| `POST` | `/books/{slug}/covers/{id}/regenerate` | Re-render from the book as it is now; optionally for another `type` or `text` |
| `POST` | `/books/{slug}/covers/{id}/publish` | Add `{id, type, url, color, theme}` to the book's `generated_covers` |
| `POST` | `/books/{slug}/covers/{id}/unpublish` | Remove it again |
| `DELETE` | `/books/{slug}/covers/{id}` | The cover, its image and its entry. 204 |
| `POST` | `/covers/upload` | Add a finished cover to its book, published: multipart `file`, `title`, `type`. See below. 201 |
| `GET` | `/cover-images/{image_id}.png` | The image, cached as immutable: each render gets a new id and URL. Not under the book, so a published `url` survives a slug change |

**Uploading a finished cover.** `POST /covers/upload` takes a multipart form with
the image `file`, the book's `title` and the reader `type`. The title is matched
exactly, ignoring case and surrounding spaces: no match is 404, and several books
with that title are a 409 that lists their slugs. The image is stored in GridFS
as PNG (up to 20 MB, any format Pillow reads), its colour is sampled as the most
common of five after reduction (the ground, on most covers), and the page theme
is derived from that colour as for generated covers. An upload is a finished
cover, so it is published at once, straight into the book's `generated_covers`,
with `source: "uploaded"` and no brief; unpublish it like any other. Regenerating
it replaces it with a generated cover of the same type.

A published cover's entry follows the cover: a PATCH or a regeneration updates it.
`color` is the brief's ground colour, and `theme` is derived from it by the same
rules the seed themes follow. Covers are linked to the book's database id, so they
survive a slug change; deleting a book leaves its covers in the collection,
unreachable.

## Formats

`GET /catalogue` lists them. These are the customary
trade formats associated with each imprint, taken from general book-trade practice
rather than from a publisher's production spec sheet. **Reconcile against the
`Umschlagvorgabe` the publisher's production department sends you before going to
press.**

| Key | Format | Trim (mm) | Binding |
|---|---|---|---|
| `rororo_taschenbuch` | rororo Taschenbuch | 118 × 190 | Taschenbuch |
| `rowohlt_paperback` | Paperback | 135 × 205 | Paperback |
| `rowohlt_hardcover` | Hardcover mit Schutzumschlag | 140 × 215 | Hardcover |
| `kiwi_paperback` | KiWi-Paperback | 125 × 190 | Paperback |
| `kiwi_taschenbuch` | KiWi-Taschenbuch | 125 × 200 | Taschenbuch |
| `kiwi_klappenbroschur` | Klappenbroschur | 135 × 215 | Klappenbroschur |
| `kiwi_hardcover` | Hardcover Leinen mit Schutzumschlag | 135 × 210 | Hardcover |
| `suhrkamp_taschenbuch` | suhrkamp taschenbuch | 108 × 177 | Taschenbuch |
| `din_a5_hardcover` | Hardcover DIN A5 | 148 × 210 | Hardcover |
| `grossformat_hardcover` | Großformatiges Hardcover | 155 × 230 | Hardcover |

What the geometry accounts for, on the front:

- **Beschnittzugabe** 3 mm on every outer edge.
- **Sicherheitsabstand** 5 mm — live type is kept inside it.
- **Überstand** 3 mm on hardcovers, so the jacket front is sized to the case
  rather than the book block.
- `marks: true` draws the trim box for proofing.

## Output

One file: `front.png`, the front with its bleed, at 300 dpi by default with the
resolution stamped in. It is rasterised from an SVG in which the type is already
outlined, so it carries no font dependency. There is no back cover, spine or flap,
and no JPEG, SVG or PDF.

## Typography

Type is shaped with HarfBuzz and emitted as outlined glyph paths, so one set of
shaped advances drives the line fitting, the SVG and the PNG — there is
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

Type is set in logical families. On a Mac each uses the system face first; those
are licensed for the machine, not for a server, so every family also has an open
face (SIL Open Font License) bundled in `src/covers/fonts/`, which is what a
deploy uses. Variable fonts ship unmodified; the weight and width are chosen at
load time.

| Family | macOS | Bundled open face |
|---|---|---|
| `geometric` | Futura | Jost |
| `grotesk` | Helvetica Neue (Condensed Black titles) | Archivo, condensed black titles |
| `grotesk_condensed` | Avenir Next Condensed | Nunito Sans at 75% width |
| `neoclassical` | Didot | Playfair Display |
| `didone` | Bodoni 72 | Bodoni Moda |
| `literary_serif` | Baskerville | Libre Baskerville |
| `humanist` | Optima | Alegreya Sans |
| `slab` | Superclarendon | Zilla Slab |
| `garalde` | — | EB Garamond: the Garamond/Sabon register of Suhrkamp, Insel, Hanser |
| `fraktur` | — | UnifrakturMaguntia titles over Garamond, for Märchen and the historical; never in capitals |
| `meta` | — | Fira Sans, Spiekermann's open successor to FF Meta |

So a cover made on a Mac and the same cover from the deployed service differ in
face, and with it in line breaks and type size, since both are fitted to the
face's metrics. Set `COVERS_SYSTEM_FONTS=0` to render with the bundled faces only
and see what a deploy will produce. The licences are next to the fonts
(`OFL-*.txt`). Every face is loaded when the app starts, and a missing one stops
it from booting, so a font problem never surfaces mid-render after an image has
been paid for.

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
`artwork`, `motif`, `genre_line`, `seed`. Pinning the template moves the
typeface with it unless you pin that too. Pinning a `motif` implies you want it
drawn.

Sixteen palettes ship in the flat, slightly austere register the idiom lives in —
`rororo_rot`, `kobalt`, `schwefel`, `pergament`, `graphit` and so on. The art
direction model may also return its own hex values.

## Speed, and where it actually goes

Measured on one cover (`kiwi_klappenbroschur`, `illustrated`, same text), before the
back cover and extra formats were dropped; the image call dominates either way:

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
| `none` | No text model at all. The image prompt is composed locally from your text behind a register preamble; palette, layout and genre line come from the deterministic brief. |

`director="none"` gives up real things: the palette is picked by hash rather than
chosen for the book, and the Gattungsbezeichnung is a keyword guess. Worth it for drafts and bulk runs,
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

About 110 tests, ~3 seconds, no network and no credentials. `tests/conftest.py`
strips the provider keys and `MONGODB_*` from the environment for every test,
because `bookworm.main` loads `.env` at import — without that the suite makes live,
billed image-generation calls. Books and covers run against in-memory `mongomock`. Tests that exercise a provider path stub the client and set
their own key.

Both live paths have been exercised end to end once: `claude-opus-5` for the brief
and `gpt-image-2` for the artwork, ~165 s wall clock for one cover.
