"""Italian owner-facing rendering of typed workflow outcomes for Telegram.

AD-018 gives this boundary one job: turn a `DispatchOutcome`, plus the display
data a repository already owns, into the exact text and controls the owner
sees. It holds no model, transport, or repository dependency, so what the owner
reads is reproducible from stored values alone.

That reproducibility is a correctness requirement rather than a convenience.
The worker commits its response under a logical key derived from the inbox
row, and a crash between the finance transaction and that commit re-dispatches
the same work. The second render must produce identical content, or the
idempotent outbox insert refuses the replay as a different response. Every
value used here therefore comes from the outcome or from committed state, and
never from wall-clock time, live queue counters, or an unordered iteration.
That rule is why `/status` answers with what the bot can do rather than with
operational figures: a counter that moved between two renders of the same work
item would turn a recovery into a conflict. Operational state belongs to the
administration and health path, which answers an operator rather than a
durable inbox item.

Callback controls are bounded twice over. Telegram allows 64 bytes of callback
data, and the gateway accepts only `a:<action-id>:<code>` with a code of at
most sixteen characters, so the branch vocabulary is a short code deliberately
distinct from the longer `CallbackAction` values.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo

from money_tracker.domain import ExpenseStatus, Money
from money_tracker.domain.profile import V1_TIMEZONE
from money_tracker.messaging import OutboundButton, PendingActionTarget
from money_tracker.workflows import (
    CallbackAction,
    DispatchOutcome,
    DispatchStatus,
    ReadTool,
    ReviewQuestion,
    SupportedCommand,
    WorkflowIntent,
)

_ACTION_CODES: dict[CallbackAction, str] = {
    CallbackAction.APPROVE_CATEGORY: "approve",
    CallbackAction.USE_BEST_EXISTING: "best",
    CallbackAction.USE_NAMED_EXISTING: "named",
    CallbackAction.REPROPOSE_CATEGORY: "again",
    CallbackAction.CANCEL_REVIEW: "cancel",
    CallbackAction.MODIFY_EXPENSE: "modify",
    CallbackAction.UNDO_EXPENSE: "undo",
}
_ACTIONS_BY_CODE = {code: action for action, code in _ACTION_CODES.items()}

_ACTION_LABELS: dict[CallbackAction, str] = {
    CallbackAction.APPROVE_CATEGORY: "Approva",
    CallbackAction.USE_BEST_EXISTING: "Usa una categoria esistente",
    CallbackAction.USE_NAMED_EXISTING: "Usa la categoria…",
    CallbackAction.REPROPOSE_CATEGORY: "Proponine un'altra",
    CallbackAction.CANCEL_REVIEW: "Annulla",
    CallbackAction.MODIFY_EXPENSE: "Modifica",
    CallbackAction.UNDO_EXPENSE: "Annulla",
}

_MONTHS = (
    "gennaio",
    "febbraio",
    "marzo",
    "aprile",
    "maggio",
    "giugno",
    "luglio",
    "agosto",
    "settembre",
    "ottobre",
    "novembre",
    "dicembre",
)

_MOVEMENT_HEADERS: dict[DispatchStatus, str] = {
    DispatchStatus.DRAFTED: "Bozza registrata.",
    DispatchStatus.APPROVED: "Categoria creata e movimento registrato.",
    DispatchStatus.REUSED: "Categoria esistente applicata e movimento registrato.",
    DispatchStatus.CORRECTED: "Movimento aggiornato.",
    DispatchStatus.VOIDED: "Movimento annullato.",
}

_HELP_TEXT = "\n".join(
    (
        "Scrivimi una spesa a parole, per esempio "
        "«ho speso 12 euro per i croccantini dei gatti».",
        "Posso anche rispondere a domande sulle spese e sui budget di un mese.",
        "Da ogni riepilogo puoi usare Modifica o Annulla.",
        "Quando propongo una nuova categoria, rispondi con i pulsanti oppure "
        "scrivi «Usa la categoria <nome>».",
        "Comandi: /help, /status.",
    )
)

_STATUS_TEXT = "\n".join(
    (
        "Il bot è attivo e associato a questo account.",
        "Elaboro un messaggio alla volta, nell'ordine in cui arrivano.",
        "Gestisco spese e rimborsi a testo, revisione della categoria, "
        "correzione, annullamento e domande su spese e budget.",
        "Non gestisco ancora le ricevute fotografate.",
    )
)

# Reason codes are a flat vocabulary at this boundary. A dispatch outcome
# carries the code alone, so the renderer cannot tell which enum produced it;
# codes shared by several enums, such as `unknown_category`, therefore have one
# text that has to be true in every context that emits them.
_QUESTIONS: dict[str, str] = {
    "already_settled": "Questa revisione di categoria è già stata chiusa.",
    "ambiguous_pending_review": (
        "Ci sono più revisioni aperte. Rispondi al messaggio della revisione "
        "che vuoi chiudere."
    ),
    "amount_unknown": "Quanto hai speso?",
    "budget_tool_unavailable": ("Non posso consultare i budget per questa domanda."),
    "category_already_exists": "Questa categoria esiste già tra quelle attive.",
    "category_approval_required": (
        "Serve la tua approvazione per creare una nuova categoria."
    ),
    "category_excluded": (
        "Questa categoria è già stata rifiutata in questa revisione."
    ),
    "category_parent_invalid": (
        "La categoria proposta non può stare sotto quel gruppo."
    ),
    "category_unresolved": ("Non riesco ad assegnare una categoria a questa spesa."),
    "correction_details_required": (
        "Dimmi cosa cambiare: importo, data, descrizione o fonte."
    ),
    "empty_input": "Non ho ricevuto nessun testo.",
    "funding_source_repeated": ("Hai indicato due volte la stessa fonte di pagamento."),
    "funding_source_unresolved": (
        "Non riconosco questa fonte di pagamento. Come si chiama esattamente?"
    ),
    "invalid_candidate": (
        "La nuova proposta non è utilizzabile. Scegli una categoria esistente "
        "oppure annulla."
    ),
    "invalid_model_output": ("Non sono riuscito a interpretare la richiesta. Riprova."),
    "low_confidence": (
        "Non ho capito la richiesta. Puoi riscriverla in modo più esplicito?"
    ),
    "model_output_invalid": ("Non sono riuscito a interpretare la richiesta. Riprova."),
    "movement_not_correctable": ("Questo movimento non può più essere corretto."),
    "movement_target_required": ("Dimmi quale movimento vuoi correggere o annullare."),
    "no_best_existing": ("Non ho categorie esistenti da proporti per questa spesa."),
    "no_correctable_movement": (
        "Non trovo un movimento recente da correggere o annullare."
    ),
    "no_default_funding_source": (
        "Non ho una fonte di pagamento predefinita. Con cosa hai pagato?"
    ),
    "no_pending_review": "Non c'è nessuna revisione di categoria in attesa.",
    "occurrence_in_future": ("La data indicata è nel futuro. Qual è la data corretta?"),
    "occurrence_unreadable": "Quando è avvenuta la spesa?",
    "polarity_disagreement": (
        "Non capisco se si tratta di una spesa o di un rimborso."
    ),
    "proposals_exhausted": (
        "Ho esaurito le proposte. Scegli una categoria esistente scrivendo "
        "«Usa la categoria <nome>», oppure annulla."
    ),
    "review_decision_required": (
        "Dimmi cosa fare della categoria proposta: approva, usa una esistente, "
        "proponine un'altra o annulla."
    ),
    "review_not_pending": "Questa revisione di categoria non è più aperta.",
    "split_does_not_reconcile": ("Le quote di pagamento non sommano al totale."),
    "tool_not_registered": (
        "Non posso rispondere a questa domanda con i dati che gestisco."
    ),
    "uncorrelated_callback": (
        "Questo pulsante non è più collegato a nulla. Riprova dall'ultimo messaggio."
    ),
    "unknown_category": (
        "Non riconosco questa categoria. Scrivi il nome esatto di una categoria attiva."
    ),
    "unknown_command": "Non conosco questo comando. Scrivi /help.",
}
_GENERIC_QUESTION = "Non ho capito la richiesta. Puoi riformularla?"

_ROUTED_TEXTS: dict[WorkflowIntent, str] = {
    WorkflowIntent.RECEIPT: (
        "Non gestisco ancora le ricevute fotografate. Scrivi la spesa a parole."
    ),
    WorkflowIntent.NON_FINANCE: (
        "Mi occupo solo delle tue spese. Scrivi /help per sapere cosa posso fare."
    ),
}
_UNSUPPORTED_TEXT = (
    "Questa richiesta non è ancora gestita. Scrivi /help per sapere cosa posso fare."
)


class ResponseKind(StrEnum):
    """Stable response family naming one logical answer per inbox item.

    The value is part of the outbox logical key, so it selects how many
    distinct responses one inbound update may ever produce. Renaming a member
    would orphan an undelivered row, which is why these are codes rather than
    prose.

    Attributes:
        MOVEMENT: Summary of a stored, corrected, or approved movement.
        VOIDED: Confirmation that a movement was voided.
        CATEGORY_REVIEW: Paused candidate question with its owner branches.
        REVIEW_CANCELLED: Confirmation that a review ended with no write.
        ANSWER: Read-only query or advice answer built from tool facts.
        HELP: Answer to the help command.
        STATUS: Answer to the status command.
        UNSUPPORTED: Acknowledgement of a route V1 does not serve yet.
        CLARIFICATION: One targeted question after a run that wrote nothing.
    """

    MOVEMENT = "movement"
    VOIDED = "voided"
    CATEGORY_REVIEW = "category_review"
    REVIEW_CANCELLED = "review_cancelled"
    ANSWER = "answer"
    HELP = "help"
    STATUS = "status"
    UNSUPPORTED = "unsupported"
    CLARIFICATION = "clarification"


def action_code(action: CallbackAction) -> str:
    """Return the bounded callback code carrying one owner branch.

    Args:
        action: Branch offered by a rendered control.

    Returns:
        The short stable code accepted by the gateway callback grammar.

    Raises:
        KeyError: If the branch has no channel control.
    """

    return _ACTION_CODES[action]


def action_for_code(code: str) -> CallbackAction | None:
    """Resolve a received callback code against the closed vocabulary.

    Args:
        code: Code decoded from callback data, still untrusted.

    Returns:
        The named branch, or None when the code names none.
    """

    return _ACTIONS_BY_CODE.get(code)


@dataclass(frozen=True, slots=True)
class MovementView:
    """Committed movement facts the owner is shown after a write.

    Every field is read from finance repositories after the mutation commits,
    so a replayed render reproduces the same summary.

    Attributes:
        amount: Signed total of the movement.
        occurred_at: Aware instant the economic event happened.
        description: Owner-visible description stored with the movement.
        status: Lifecycle state reached by the mutation.
        category_names: Purpose allocations, empty while a review is open.
        funding_source_names: Payment allocations in stored order.
    """

    amount: Money
    occurred_at: datetime
    description: str
    status: ExpenseStatus
    category_names: tuple[str, ...] = ()
    funding_source_names: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ResponseTarget:
    """Correlation a rendered control needs before it can be answered.

    Attributes:
        target_type: Trusted object class the controls act on.
        target_id: Stable identifier of that object.
        allowed_actions: Callback codes this response may accept later.
    """

    target_type: PendingActionTarget
    target_id: UUID
    allowed_actions: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class OwnerResponse:
    """One rendered logical response awaiting durable identity.

    The renderer owns content and its digest; the worker owns identity, chat
    scope, and the logical key, because only it knows the inbox row.

    Attributes:
        kind: Response family selecting the logical key suffix.
        text: Plain Italian text sent to the owner.
        buttons: Bounded controls, empty when the response offers none.
        target: Correlation to persist when the response offers controls.
    """

    kind: ResponseKind
    text: str
    buttons: tuple[OutboundButton, ...] = ()
    target: ResponseTarget | None = None

    def __post_init__(self) -> None:
        """Reject a response whose controls could never be answered."""

        if not self.text.strip():
            raise ValueError("an owner response cannot be blank")
        if bool(self.buttons) != (self.target is not None):
            raise ValueError("controls and their correlation must appear together")

    @property
    def content_hash(self) -> str:
        """Return the digest proving two renders produced the same response.

        Returns:
            Lowercase SHA-256 over the canonical kind, text, and controls.
        """

        canonical = json.dumps(
            {
                "kind": self.kind.value,
                "text": self.text,
                "buttons": [
                    {"label": button.label, "callback_data": button.callback_data}
                    for button in self.buttons
                ],
            },
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class TelegramResponseRenderer:
    """Map one typed outcome and its display data to what the owner reads.

    Attributes:
        The renderer is stateless. It is a class rather than a function because
        the worker injects it as the presentation port AD-018 keeps separate
        from dispatch, so another channel can supply its own implementation.
    """

    def render(
        self,
        outcome: DispatchOutcome,
        *,
        action_id: UUID,
        movement: MovementView | None = None,
        question: ReviewQuestion | None = None,
    ) -> OwnerResponse:
        """Render the single logical response owed for one dispatched update.

        Args:
            outcome: Typed terminal or paused result of the work unit.
            action_id: Stable correlation reference the controls carry. It must
                be derived from the inbox row rather than generated, so a
                replayed render produces identical callback data.
            movement: Committed movement facts, required by every status that
                reports a stored, corrected, approved, or voided movement.
            question: Paused review question, required while a candidate
                awaits the owner.

        Returns:
            One response carrying its text, controls, and correlation.

        Raises:
            ValueError: If the status needs display data that is missing.
        """

        status = outcome.status
        if status is DispatchStatus.AWAITING_CATEGORY_APPROVAL:
            if question is None or outcome.proposal_id is None:
                raise ValueError("a paused review must render its question")
            return self._review(question, outcome.proposal_id, action_id)
        if status is DispatchStatus.CANCELLED:
            return OwnerResponse(
                kind=ResponseKind.REVIEW_CANCELLED,
                text=(
                    "Revisione annullata: nessuna categoria creata e nessun "
                    "movimento registrato."
                ),
            )
        if status is DispatchStatus.VOIDED:
            return OwnerResponse(
                kind=ResponseKind.VOIDED,
                text=self._movement_text(status, _required(movement)),
            )
        if status in _MOVEMENT_HEADERS:
            return self._movement(status, _required(movement), action_id, outcome)
        if status is DispatchStatus.ANSWERED:
            return OwnerResponse(
                kind=ResponseKind.ANSWER,
                text=_answer_text(outcome.executed_tool, outcome.facts),
            )
        if status is DispatchStatus.ROUTED:
            return _routed(outcome)
        return OwnerResponse(
            kind=ResponseKind.CLARIFICATION,
            text=_QUESTIONS.get(outcome.clarification_reason or "", _GENERIC_QUESTION),
        )

    def _movement(
        self,
        status: DispatchStatus,
        movement: MovementView,
        action_id: UUID,
        outcome: DispatchOutcome,
    ) -> OwnerResponse:
        """Render a stored movement with its correction and undo controls."""

        if outcome.expense_id is None:
            raise ValueError("a movement summary must reference its movement")
        actions = (CallbackAction.MODIFY_EXPENSE, CallbackAction.UNDO_EXPENSE)
        return OwnerResponse(
            kind=ResponseKind.MOVEMENT,
            text=self._movement_text(status, movement),
            buttons=_buttons(actions, action_id),
            target=ResponseTarget(
                target_type=PendingActionTarget.MOVEMENT,
                target_id=outcome.expense_id,
                allowed_actions=tuple(action_code(action) for action in actions),
            ),
        )

    @staticmethod
    def _movement_text(status: DispatchStatus, movement: MovementView) -> str:
        """Render amount, date, purpose, and payment as J2 requires."""

        lines = [
            _MOVEMENT_HEADERS[status],
            f"{_amount(movement.amount)} · {movement.description}",
            f"Data: {_date(movement.occurred_at)}",
        ]
        if movement.category_names:
            lines.append(f"Categoria: {', '.join(movement.category_names)}")
        if movement.funding_source_names:
            lines.append(f"Fonte: {', '.join(movement.funding_source_names)}")
        if movement.status is ExpenseStatus.DRAFT:
            lines.append("Resta in bozza finché non viene registrata.")
        return "\n".join(lines)

    @staticmethod
    def _review(
        question: ReviewQuestion,
        proposal_id: UUID,
        action_id: UUID,
    ) -> OwnerResponse:
        """Render the paused candidate and only the branches it still offers."""

        candidate = question.candidate_name
        if question.parent_name is not None:
            candidate = f"{candidate} (in {question.parent_name})"
        lines = [
            "Nessuna categoria attiva descrive questa spesa.",
            f"Proposta: {candidate}",
            f"Motivo: {question.rationale}",
        ]
        if question.alternative_names:
            lines.append(
                f"Alternative esistenti: {', '.join(question.alternative_names)}"
            )
        lines.append("Puoi anche rispondere «Usa la categoria <nome>».")
        offered = tuple(
            action
            for action in question.offered_actions()
            if action is not CallbackAction.USE_NAMED_EXISTING
        )
        return OwnerResponse(
            kind=ResponseKind.CATEGORY_REVIEW,
            text="\n".join(lines),
            buttons=_buttons(offered, action_id),
            target=ResponseTarget(
                target_type=PendingActionTarget.CATEGORY_REVIEW,
                target_id=proposal_id,
                allowed_actions=tuple(action_code(action) for action in offered),
            ),
        )


def _required(movement: MovementView | None) -> MovementView:
    """Return movement facts a movement-reporting status cannot omit."""

    if movement is None:
        raise ValueError("this outcome must render its movement summary")
    return movement


def _routed(outcome: DispatchOutcome) -> OwnerResponse:
    """Answer a route that reached no specialist, including channel commands.

    Pairing is a gateway transition rather than a workflow, so a `/pair`
    reaching this point can only come from an owner who is already bound.
    """

    command = outcome.routing.command
    if command is SupportedCommand.HELP:
        return OwnerResponse(kind=ResponseKind.HELP, text=_HELP_TEXT)
    if command is SupportedCommand.STATUS:
        return OwnerResponse(kind=ResponseKind.STATUS, text=_STATUS_TEXT)
    if command is SupportedCommand.PAIR:
        return OwnerResponse(
            kind=ResponseKind.STATUS,
            text="Questo account è già associato al bot.",
        )
    return OwnerResponse(
        kind=ResponseKind.UNSUPPORTED,
        text=_ROUTED_TEXTS.get(outcome.routing.intent, _UNSUPPORTED_TEXT),
    )


def _buttons(
    actions: tuple[CallbackAction, ...],
    action_id: UUID,
) -> tuple[OutboundButton, ...]:
    """Build controls whose callback data fits the gateway grammar."""

    return tuple(
        OutboundButton(
            label=_ACTION_LABELS[action],
            callback_data=f"a:{action_id}:{action_code(action)}",
        )
        for action in actions
    )


def _amount(amount: Money) -> str:
    """Render one exact amount in Italian notation without floating point."""

    units, cents = divmod(abs(amount.minor_units), 100)
    grouped = f"{units:,}".replace(",", ".")
    sign = "-" if amount.minor_units < 0 else ""
    return f"{sign}{grouped},{cents:02d} €"


def _date(occurred_at: datetime) -> str:
    """Render the owner's local calendar day of one aware instant."""

    return occurred_at.astimezone(ZoneInfo(V1_TIMEZONE)).strftime("%d/%m/%Y")


def _month(period: str) -> str:
    """Render a `YYYY-MM` boundary value as an Italian month and year."""

    year, month = period.split("-")
    return f"{_MONTHS[int(month) - 1]} {int(year)}"


def _facts_amount(value: Any) -> str:
    """Render one amount object produced by a read tool."""

    if not isinstance(value, dict) or "minor_units" not in value:
        raise ValueError("read facts carry a malformed amount")
    return _amount(Money(int(value["minor_units"]), str(value.get("currency", "EUR"))))


def _split_text(split: Any) -> str:
    """Name only the economic sources that actually contributed."""

    if not isinstance(split, dict):
        raise ValueError("read facts carry a malformed split")
    parts = [
        f"{label} {_facts_amount(split[key])}"
        for key, label in (
            ("personal_funds", "fondi personali"),
            ("benefit", "benefit"),
            ("other", "altro"),
        )
        if int(split[key]["minor_units"]) != 0
    ]
    return ", ".join(parts) if parts else "nessuna spesa registrata"


def _budget_position(status: Any) -> str:
    """Render one budget position, naming an overspend as such."""

    remaining = int(status["remaining"]["minor_units"])
    spent = _facts_amount(status["posted_spend"])
    target = _facts_amount(status["target"])
    if remaining < 0:
        return f"{spent} su {target}, {_amount(Money(-remaining))} oltre il budget"
    return f"{spent} su {target}, {_facts_amount(status['remaining'])} disponibili"


def _answer_text(tool: ReadTool | None, facts: dict[str, Any] | None) -> str:
    """Narrate only the facts the admitted read tool returned.

    Args:
        tool: Tool whose deterministic output supplied every figure.
        facts: JSON-compatible result of that tool.

    Returns:
        Concise Italian prose restating the tool result.

    Raises:
        ValueError: If the answer carries no tool result to narrate.
    """

    if tool is None or facts is None:
        raise ValueError("an answered outcome must carry its tool facts")
    period = _month(str(facts["period"]))
    if tool is ReadTool.CATEGORY_SPENDING:
        return (
            f"{facts['category_name']} a {period}: "
            f"{_facts_amount(facts['posted_spend'])} "
            f"({_split_text(facts['split'])})."
        )
    if tool is ReadTool.BUDGET_STATUS:
        status = facts["status"]
        if status is None:
            return f"Nessun budget configurato per {facts['category_name']} a {period}."
        return (
            f"Budget {facts['category_name']} a {period}: {_budget_position(status)}."
        )
    if tool is ReadTool.FUNDING_CLASS_SPLIT:
        sources = ", ".join(
            f"{source['name']} {_facts_amount(source['posted_spend'])}"
            for source in facts["sources"]
        )
        text = f"Spese di {period}: {_split_text(facts['split'])}."
        return f"{text}\nPer fonte: {sources}." if sources else text
    lines = [f"Budget di {period}:"]
    lines.extend(
        f"- {status['category_name']}: {_budget_position(status)}"
        for status in facts["statuses"]
    )
    if len(lines) == 1:
        lines.append("- nessun budget configurato")
    unbudgeted = facts["unbudgeted_category_names"]
    if unbudgeted:
        lines.append(f"Senza budget: {', '.join(unbudgeted)}.")
    return "\n".join(lines)


__all__ = [
    "MovementView",
    "OwnerResponse",
    "ResponseKind",
    "ResponseTarget",
    "TelegramResponseRenderer",
    "action_code",
    "action_for_code",
]
