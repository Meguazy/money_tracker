"""Accepted versioned V1 category taxonomy with deterministic identifiers."""

from __future__ import annotations

from uuid import UUID, uuid5

from money_tracker.domain.categories import Category, Taxonomy

TAXONOMY_VERSION = "v1-2026-09-01"
_TAXONOMY_NAMESPACE = UUID("f4f1c741-a34f-4b22-9b42-a0ad8b686391")


def _category_id(slug: str) -> UUID:
    """Derive a stable seed identifier from the accepted taxonomy version.

    Args:
        slug: Stable code-owned category slug.

    Returns:
        Deterministic UUID for the seed node.
    """

    return uuid5(_TAXONOMY_NAMESPACE, f"{TAXONOMY_VERSION}:{slug}")


def initial_taxonomy() -> Taxonomy:
    """Build the accepted 10-root and 14-child Italian V1 taxonomy.

    Returns:
        Validated taxonomy with stable deterministic identifiers and inclusion
        descriptions.
    """

    alimentazione = _category_id("alimentazione")
    casa = _category_id("casa-e-utenze")
    mobilita = _category_id("mobilita")
    salute = _category_id("salute-e-cura-personale")
    animali = _category_id("animali")
    tempo_libero = _category_id("tempo-libero")
    categories = (
        Category(
            alimentazione,
            "Alimentazione",
            "Consumi alimentari domestici e pasti preparati.",
            seeded=True,
        ),
        Category(
            _category_id("spesa-alimentare"),
            "Spesa alimentare",
            "Alimenti e bevande non alcoliche acquistati per il consumo domestico.",
            alimentazione,
            seeded=True,
        ),
        Category(
            _category_id("bar-ristoranti-asporto"),
            "Bar, ristoranti e asporto",
            "Pasti e bevande preparati fuori casa, consegna e asporto.",
            alimentazione,
            seeded=True,
        ),
        Category(
            casa,
            "Casa e utenze",
            "Abitazione, servizi domestici, comunicazioni e manutenzione della casa.",
            seeded=True,
        ),
        Category(
            _category_id("abitazione-condominio"),
            "Abitazione e condominio",
            "Affitto, condominio e costi direttamente legati all'abitazione.",
            casa,
            seeded=True,
        ),
        Category(
            _category_id("bollette-comunicazioni"),
            "Bollette e comunicazioni",
            "Energia, acqua, connettivita e servizi di comunicazione domestici.",
            casa,
            seeded=True,
        ),
        Category(
            _category_id("prodotti-casa-arredo-manutenzione"),
            "Prodotti casa, arredo e manutenzione",
            "Pulizia, consumabili domestici, mobili e manutenzione della casa.",
            casa,
            seeded=True,
        ),
        Category(
            mobilita,
            "Mobilità",
            "Trasporti personali, spostamenti e costi dei veicoli.",
            seeded=True,
        ),
        Category(
            _category_id("trasporto-pubblico"),
            "Trasporto pubblico",
            "Biglietti e abbonamenti per trasporto pubblico.",
            mobilita,
            seeded=True,
        ),
        Category(
            _category_id("auto-moto"),
            "Auto e moto",
            "Carburante, parcheggio, manutenzione e costi di auto o moto.",
            mobilita,
            seeded=True,
        ),
        Category(
            salute,
            "Salute e cura personale",
            "Salute, trattamenti e cura quotidiana della persona.",
            seeded=True,
        ),
        Category(
            _category_id("farmaci-prestazioni-sanitarie"),
            "Farmaci e prestazioni sanitarie",
            "Farmaci, visite, esami e trattamenti sanitari.",
            salute,
            seeded=True,
        ),
        Category(
            _category_id("cura-personale"),
            "Cura personale",
            "Igiene, cosmetica, parrucchiere e cura non sanitaria della persona.",
            salute,
            seeded=True,
        ),
        Category(
            animali,
            "Animali",
            "Cura e mantenimento degli animali domestici.",
            seeded=True,
        ),
        Category(
            _category_id("cibo-accessori-animali"),
            "Cibo e accessori",
            "Alimenti, lettiere, giochi e accessori per animali domestici.",
            animali,
            seeded=True,
        ),
        Category(
            _category_id("veterinario"),
            "Veterinario",
            "Visite, farmaci e trattamenti veterinari.",
            animali,
            seeded=True,
        ),
        Category(
            tempo_libero,
            "Tempo libero",
            "Intrattenimento, hobby, sport, cultura e viaggi.",
            seeded=True,
        ),
        Category(
            _category_id("abbonamenti"),
            "Abbonamenti",
            "Servizi digitali e di intrattenimento con pagamento ricorrente.",
            tempo_libero,
            seeded=True,
        ),
        Category(
            _category_id("sport-hobby-cultura"),
            "Sport, hobby e cultura",
            "Attivita sportive, hobby, spettacoli e consumi culturali.",
            tempo_libero,
            seeded=True,
        ),
        Category(
            _category_id("viaggi-alloggi"),
            "Viaggi e alloggi",
            "Pernottamenti e servizi acquistati principalmente per un viaggio.",
            tempo_libero,
            seeded=True,
        ),
        Category(
            _category_id("acquisti-personali"),
            "Acquisti personali",
            "Abbigliamento, calzature, elettronica e beni personali non "
            "classificati altrove.",
            seeded=True,
        ),
        Category(
            _category_id("formazione"),
            "Formazione",
            "Corsi, libri e costi il cui scopo principale e l'apprendimento.",
            seeded=True,
        ),
        Category(
            _category_id("regali-donazioni"),
            "Regali e donazioni",
            "Regali, donazioni e sostegno economico dato ad altri.",
            seeded=True,
        ),
        Category(
            _category_id("finanza-obblighi"),
            "Finanza e obblighi",
            "Assicurazioni, imposte, commissioni, interessi e oneri finanziari "
            "inevitabili.",
            seeded=True,
        ),
    )
    return Taxonomy(categories)
