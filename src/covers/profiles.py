"""Lesetyp profiles: what the cover-concept brief (Prompt B) works from, per mood.

Each profile tells the art director how a reader type is won over: which motifs
to reach for, the typical twist, the colour strategy, and the fixed style and
negative wording that goes into every image prompt for that type. The keys are
the mood keys; the German label is what the prompts call the type.

A first draft, written from the moods and the house example. Adjust freely: the
wording here goes into the prompts verbatim.
"""

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Profile:
    label: str
    #: How motifs are chosen from the Buchkern.
    motif_logic: str
    #: The kind of twist ("Kniff") this reader type responds to.
    twist: str
    colour_strategy: str
    composition: str
    #: English, into every image prompt for this type.
    style: str
    #: English, into every image prompt's "Avoid" line.
    avoid: str
    #: DISKURS: modes, one per concept, as different as possible.
    modes: dict[str, str] = field(default_factory=dict)
    #: SPANNUNG: registers chosen by the Buchkern's genre hint.
    registers: dict[str, str] = field(default_factory=dict)


PROFILES: dict[str, Profile] = {
    "heart": Profile(
        label="HERZ",
        motif_logic=(
            "Motive, an denen eine Beziehung oder ein Sehnsuchtsort hängt: ein Ort, ein "
            "Gegenstand, ein Detail, das zwei Menschen verbindet. Figuren nur klein, im "
            "Anschnitt oder als Spur (zwei Tassen, ein zweites Fahrrad)."
        ),
        twist="Eine zärtliche Irritation: etwas fehlt, ist doppelt, steht verkehrt – still, nicht laut.",
        colour_strategy=(
            "Warm und hell, eine sonnige oder abendliche Grundfarbe mit einem kräftigen, "
            "herzlichen Akzent; nie grau, nie kalt."
        ),
        composition=(
            "one clear motif at a generous scale, soft depth, open sky or wall as a calm "
            "area for the type"
        ),
        style=(
            "warm illustrated or painterly image with hand-made texture, gouache or soft "
            "oil, golden or late-afternoon light, intimate scale, a small telling detail, "
            "figures only small, cropped or as traces"
        ),
        avoid=(
            "kitsch hearts, rose petals, couples kissing, stock-photo smiles, pastel "
            "sugar, sunset cliché, cartoon style, glossy 3D"
        ),
    ),
    "suspense": Profile(
        label="SPANNUNG",
        motif_logic=(
            "Motive, die eine Bedrohung versprechen, ohne sie zu zeigen: ein Ort kurz "
            "bevor etwas geschieht, ein Gegenstand, der nicht dorthin gehört, eine Spur."
        ),
        twist="Ein Detail, das nicht stimmt: das eine Licht, das an ist; die eine Tür, die offen steht.",
        colour_strategy=(
            "Dunkle, gesättigte Grundfarbe (Nachtblau, Petrol, Violettgrau) mit genau "
            "einem glühenden Akzent."
        ),
        composition=(
            "strong diagonal or deep perspective, low horizon, a dark calm area of sky or "
            "wall that carries the type"
        ),
        style=(
            "cinematic image full of momentum, a place or object that matters to the story "
            "caught in the moment just before everything changes, strong directional light, "
            "deep saturated base colour with one glowing accent, painterly or photographic "
            "realism with film grain, figures only cropped or as small details"
        ),
        avoid=(
            "weapons, blood, gore, crime-scene tape, lone figure seen from behind looking "
            "at the horizon, sunset kitsch, pastel softness, static composition, flat "
            "lighting, cartoon style, HDR"
        ),
        registers={
            "düster": (
                "night, fog and cold light; menace held back; the uncanny in an ordinary place"
            ),
            "dramatisch": (
                "weather and wind, the last warm light before a storm, an idyll about to break"
            ),
        },
    ),
    "trend": Profile(
        label="TREND",
        motif_logic=(
            "Ein einziges, ikonisches Motiv, das auf Daumennagelgröße funktioniert: ein "
            "Gegenstand oder Symbol aus dem Stoff, freigestellt oder grafisch überhöht."
        ),
        twist="Überhöhung: ein Gegenstand, der zu groß, zu leuchtend oder verzaubert ist.",
        colour_strategy=(
            "Satt und selbstbewusst: eine kräftige Grundfarbe mit Komplementärakzent, "
            "gern mit Leuchten oder Glanz."
        ),
        composition=(
            "a single iconic motif centred or boldly cropped, graphic silhouette, a clean "
            "flat area for the type"
        ),
        style=(
            "bold graphic illustration made to stop the scroll, saturated colour, strong "
            "silhouette, ornamental or luminous detail, contemporary bestseller look"
        ),
        avoid=(
            "muddy colour, busy collage, generic fantasy castle, faces in close-up, "
            "photographic stock look, clutter"
        ),
    ),
    "discourse": Profile(
        label="DISKURS",
        motif_logic=(
            "Ein Gegenstand oder Zeichen, das den Gedanken des Buchs verdichtet; das "
            "Konkrete als Metapher, nie die Illustration der Handlung."
        ),
        twist="Ein begrifflicher Widerspruch als Bild: ein Ding im falschen Material, Maßstab oder Kontext.",
        colour_strategy="Reduziert: zwei oder drei flache Farben, eine davon deutlich; viel ruhiger Grund.",
        composition=(
            "one object or sign isolated on a flat ground, generous empty space, precise "
            "placement"
        ),
        style=(
            "restrained, confident and conceptual, flat colour, precise edges, no decoration"
        ),
        avoid=(
            "illustrating the plot, stock imagery, gradients, drop shadows, visual noise, "
            "cartoon style"
        ),
        modes={
            "a": "object study: one real object photographed or painted with museum-like calm",
            "b": "graphic reduction: the motif reduced to flat shapes and one strong sign",
            "c": "material irritation: the motif rendered in an unexpected material or scale",
        },
    ),
}
