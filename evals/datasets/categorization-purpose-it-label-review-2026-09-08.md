# categorization-purpose-it 1.1.0 — label review of the disputed cases

- Reviewed against: the seed taxonomy's own inclusion descriptions
  (`domain/taxonomy_seed.py`) and the Step 3 label clarification in
  [the product baseline](../../docs/product/domain-and-taxonomy.md).
- Scope: the 20 cases `candidate-category-fit-it-v6` disagrees with in run
  `CAT-20260908-153538-8281a737`. The other 133 are not reviewed here.
- Effect: none applied. This records what the contract settles and what it does
  not; changing a label needs a new dataset version and the owner's decision on
  the open items below.

## Settled by the contract, label confirmed — 8 cases

The boundary is wrong and the label stands. Each row quotes the text that
settles it.

| Case | Message | Label | What settles it |
| --- | --- | --- | --- |
| CAT-EXI-046 | Lavaggio auto | `Auto e moto` | `Auto e moto` covers "manutenzione e costi di auto o moto"; the answer given, `Prodotti casa…`, is scoped to "manutenzione **della casa**" |
| CAT-SAF-012 | Croccantini da Arcaplanet | `Cibo e accessori` | "Alimenti… **per animali domestici**", against `Spesa alimentare` = "consumo **domestico**" |
| CAT-EXI-020 | Quota straordinaria per il tetto | `Abitazione e condominio` | "Affitto, **condominio** e costi direttamente legati all'abitazione" |
| CAT-EXI-033 | Elettricista per la presa | `Prodotti casa, arredo e manutenzione` | "manutenzione della casa" |
| CAT-EXI-019 | Rata del mutuo | `Abitazione e condominio` | "costi direttamente legati all'abitazione" |
| CAT-SAF-001 | Spesa al supermercato | `Spesa alimentare` | "Alimenti e bevande… per il consumo domestico" |
| CAT-EXI-097 | Bomboniere per il matrimonio | `Regali e donazioni` | "Regali… dato ad altri" |
| CAT-PRO-009 | Toelettatura del cane | propose | `Cura personale` is "cura non sanitaria **della persona**"; `Cibo e accessori` lists goods, not services; `Veterinario` is health. No node covers it |

Six of the eight are the boundary answering the parent of the right node, which
is a boundary defect and not a labelling question.

## Settled by the contract, and the label disagrees with it — 2 cases

These are the cases where a category description already covers the purpose, so
"propose a new category" contradicts the taxonomy the proposal would extend.

| Case | Message | Label | The description that already covers it |
| --- | --- | --- | --- |
| CAT-PRO-008 | Colf per le pulizie | propose | `Prodotti casa, arredo e manutenzione` opens with "**Pulizia**, consumabili domestici, mobili e manutenzione della casa" |
| CAT-PRO-004 | Sigarette | propose | `Acquisti personali` = "Abbigliamento, calzature, elettronica e **beni personali non classificati altrove**", which is an explicit catch-all |

The boundary answered exactly those two categories. Either the labels change, or
the two descriptions do — but as written, the contract and the labels disagree
and the boundary is following the contract.

`Acquisti personali` deserves a decision of its own: the product baseline states
"There is no active catch-all `Altro`", while this category's own description
ends in "non classificati altrove". Whether that phrase is a catch-all decides
several proposal cases at once.

## Not settled — needs the owner

### Unstated reading purpose — 2 cases

`CAT-EXI-073` `Ho comprato un libro 18,90` and `CAT-SAF-004` `Ho comprato su
Amazon un libro, 18 euro`, both labelled `Sport, hobby e cultura`, both answered
`Formazione`.

The baseline now says "Learning materials use `Formazione`; recreational reading
uses `Sport, hobby e cultura`", and neither message states a purpose. The
`Formazione` description lists "**libri**" explicitly and `Sport, hobby e
cultura` does not, so a boundary reading the descriptions literally reaches
`Formazione` — which is what happened, on both cases, for every candidate
measured.

The baseline already records this as open. The choice is between naming a
default reading for an unstated purpose, and treating these as unscorable the
way `ingestion/expense-it` treats a merchant that names a kind of place rather
than a business. The second is more honest and this suite has no mechanism for
it.

### Remaining proposal labels — 7 cases

`CAT-PRO-001` baby-sitter, `-003` mensa scolastica, `-005` trasloco,
`-006` notaio, `-010` commercialista, `-011` attrezzi da giardinaggio, and the
`CAT-EXI-102` parking fine.

These turn on one unanswered question: how much of a purpose an existing
description has to cover before proposing is wrong. `Finanza e obblighi` reads
"commissioni… e oneri finanziari **inevitabili**", which a notary's fee arguably
is and a parking fine plainly is not, yet the fine is labelled there. Until the
adequacy rule is stated, these are judgment calls the labels record without
justifying, and the boundary's disagreement with them is not evidence about the
boundary.

## What this review does not claim

It reviews 20 of 153 labels, chosen because one candidate disagreed with them.
The other 133 are unreviewed, and a case the boundary happens to agree with has
not been checked against the contract at all.
