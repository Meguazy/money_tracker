import {
  Alert,
  Badge,
  Button,
  Card,
  Fieldset,
  Group,
  NativeSelect,
  Paper,
  SimpleGrid,
  Stack,
  Table,
  Text,
  TextInput,
  Title,
} from "@mantine/core";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { type FormEvent, useState } from "react";

import {
  cancelExpense,
  createExpense,
  type ExpenseInput,
  loadTransactions,
  postExpense,
  replaceExpense,
  type Transaction,
  type TransactionRegister,
} from "./api";
import { AccessibleTableScrollContainer } from "./AccessibleTableScrollContainer";
import {
  currentRomeMonth,
  formatExactEuro,
  formatMoney,
  formatOccurredAt,
  minorUnitsToEuroInput,
  parseEuroToMinorUnits,
  romeDateTimeInput,
  romeDateTimeInputToInstant,
} from "./format";

type TransactionApis = {
  load: typeof loadTransactions;
  create: typeof createExpense;
  replace: typeof replaceExpense;
  post: typeof postExpense;
  cancel: typeof cancelExpense;
};

type AllocationRow = {
  key: number;
  referenceId: string;
  amount: string;
};

type CategoryEditorRow = AllocationRow & {
  original: Transaction["category_allocations"][number] | null;
};

type ScalarEditorField = "amount" | "occurredAt" | "description" | "merchant";

type AllocationDimension = "categorie" | "fonti";

const OWNER_PROVENANCE = "owner:web:v1";

let nextAllocationKey = 0;

type EditorState = {
  amount: string;
  occurredAt: string;
  description: string;
  merchant: string;
  categoryAllocations: CategoryEditorRow[];
  paymentAllocations: AllocationRow[];
};

const defaultApis: TransactionApis = {
  load: loadTransactions,
  create: createExpense,
  replace: replaceExpense,
  post: postExpense,
  cancel: cancelExpense,
};

const statusLabels = {
  draft: "Bozza",
  needs_review: "Da verificare",
  posted: "Registrato",
  voided: "Annullato",
  discarded: "Scartato",
} as const;

const statusColors = {
  draft: "yellow",
  needs_review: "orange",
  posted: "teal",
  voided: "gray",
  discarded: "gray",
} as const;

function allocationRow(referenceId = "", amount = ""): AllocationRow {
  nextAllocationKey += 1;
  return { key: nextAllocationKey, referenceId, amount };
}

function categoryAllocationRow(
  referenceId = "",
  amount = "",
  original: CategoryEditorRow["original"] = null,
): CategoryEditorRow {
  return { ...allocationRow(referenceId, amount), original };
}

function analyzeAllocations(rows: AllocationRow[]): {
  amounts: Array<number | null>;
  sum: bigint | null;
} {
  const amounts = rows.map((row) => parseEuroToMinorUnits(row.amount));
  if (amounts.some((amount) => amount === null)) {
    return { amounts, sum: null };
  }
  return {
    amounts,
    sum: amounts.reduce(
      (total, amount) => total + BigInt(amount as number),
      0n,
    ),
  };
}

function validateAllocations(
  rows: AllocationRow[],
  total: number,
  dimension: AllocationDimension,
): { amounts: number[] | null; errors: string[] } {
  const analysis = analyzeAllocations(rows);
  const errors: string[] = [];
  const referenceLabel = dimension === "categorie" ? "categoria" : "fonte";
  if (rows.length === 0) {
    errors.push(`Aggiungi almeno una riga delle ${dimension}.`);
  }
  if (rows.some((row) => row.referenceId.length === 0)) {
    errors.push(
      `Seleziona una ${referenceLabel} in ogni riga delle ${dimension}.`,
    );
  }
  if (analysis.sum === null) {
    errors.push(
      `Inserisci un importo non nullo, con al massimo due decimali, in ogni riga delle ${dimension}.`,
    );
  }
  const selected = rows
    .map((row) => row.referenceId)
    .filter((referenceId) => referenceId.length > 0);
  if (new Set(selected).size !== selected.length) {
    errors.push(`Ogni ${referenceLabel} può comparire una sola volta.`);
  }
  if (
    analysis.amounts.some(
      (amount) => amount !== null && (amount > 0) !== (total > 0),
    )
  ) {
    errors.push(
      `Gli importi delle ${dimension} devono avere lo stesso segno del totale.`,
    );
  }
  if (analysis.sum !== null && analysis.sum !== BigInt(total)) {
    errors.push(
      `La ripartizione delle ${dimension} somma ${formatExactEuro(analysis.sum)}, mentre il totale è ${formatExactEuro(BigInt(total))}.`,
    );
  }
  // Each amount stays aligned with its row only while every row parsed, so the
  // caller receives the exact cents solely when nothing is left to correct.
  return {
    amounts: errors.length === 0 ? (analysis.amounts as number[]) : null,
    errors,
  };
}

// A correction that leaves the date and time untouched keeps the recorded
// instant, including seconds and the occurrence of a repeated autumn hour that
// the minute-precision Rome wall time cannot distinguish.
function submittedOccurredAt(
  value: string,
  editing: Transaction | null,
): Date | null {
  if (editing !== null) {
    const recorded = new Date(editing.occurred_at);
    if (value === romeDateTimeInput(recorded)) {
      return recorded;
    }
  }
  return romeDateTimeInputToInstant(value);
}

function emptyEditor(): EditorState {
  return {
    amount: "",
    occurredAt: romeDateTimeInput(),
    description: "",
    merchant: "",
    categoryAllocations: [categoryAllocationRow()],
    paymentAllocations: [allocationRow()],
  };
}

export function TransactionsPage({
  apis = defaultApis,
}: {
  apis?: TransactionApis;
}) {
  const queryClient = useQueryClient();
  const [period, setPeriod] = useState(currentRomeMonth);
  const [editor, setEditor] = useState<EditorState>(emptyEditor);
  const [editing, setEditing] = useState<Transaction | null>(null);
  const [formErrors, setFormErrors] = useState<string[]>([]);
  const register = useQuery({
    queryKey: ["transactions", period],
    queryFn: ({ signal }) => apis.load(period, signal),
    retry: false,
  });
  const refresh = async () => {
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: ["transactions"] }),
      queryClient.invalidateQueries({ queryKey: ["monthly-overview"] }),
      queryClient.invalidateQueries({ queryKey: ["range-overview"] }),
    ]);
  };
  const save = useMutation({
    mutationFn: async (input: ExpenseInput) =>
      editing === null
        ? apis.create(input)
        : apis.replace(editing.id, {
            ...input,
            expected_version: editing.version,
          }),
    onSuccess: async () => {
      setEditor(emptyEditor());
      setEditing(null);
      setFormErrors([]);
      await refresh();
    },
  });
  const action = useMutation({
    mutationFn: ({
      transaction,
      kind,
    }: {
      transaction: Transaction;
      kind: "post" | "cancel";
    }) =>
      kind === "post"
        ? apis.post(transaction.id, transaction.version)
        : apis.cancel(transaction.id, transaction.version),
    onSuccess: refresh,
  });

  function update(field: ScalarEditorField, value: string | null) {
    setEditor((current) => ({ ...current, [field]: value ?? "" }));
  }

  function updateCategoryRow(
    key: number,
    field: "referenceId" | "amount",
    value: string,
  ) {
    setEditor((current) => ({
      ...current,
      categoryAllocations: current.categoryAllocations.map((row) =>
        row.key === key ? { ...row, [field]: value } : row,
      ),
    }));
  }

  function updatePaymentRow(
    key: number,
    field: "referenceId" | "amount",
    value: string,
  ) {
    setEditor((current) => ({
      ...current,
      paymentAllocations: current.paymentAllocations.map((row) =>
        row.key === key ? { ...row, [field]: value } : row,
      ),
    }));
  }

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const minorUnits = parseEuroToMinorUnits(editor.amount);
    const errors: string[] = [];
    if (minorUnits === null) {
      errors.push("Inserisci un totale non nullo con al massimo due decimali.");
    }
    const occurredAt = submittedOccurredAt(editor.occurredAt, editing);
    if (occurredAt === null) {
      errors.push("Inserisci una data e un'ora valide.");
    }
    if (editor.description.trim().length === 0) {
      errors.push("Inserisci una descrizione.");
    }
    if (minorUnits === null) {
      setFormErrors(errors);
      return;
    }
    const categories = validateAllocations(
      editor.categoryAllocations,
      minorUnits,
      "categorie",
    );
    const payments = validateAllocations(
      editor.paymentAllocations,
      minorUnits,
      "fonti",
    );
    errors.push(...categories.errors, ...payments.errors);
    const categoryAmounts = categories.amounts;
    const paymentAmounts = payments.amounts;
    if (
      errors.length > 0 ||
      occurredAt === null ||
      categoryAmounts === null ||
      paymentAmounts === null
    ) {
      setFormErrors(errors);
      return;
    }
    const input: ExpenseInput = {
      amount: { minor_units: minorUnits, currency: "EUR" },
      occurred_at: occurredAt.toISOString(),
      description: editor.description.trim(),
      merchant: editor.merchant.trim() || null,
      category_allocations: editor.categoryAllocations.map((row, index) => {
        // Provenance, evidence, and the override flag explain who chose this
        // purpose, so replacing the category replaces them too. A receipt line
        // records which purchased item the money came from and survives a
        // recategorization of that same line.
        const original = row.original;
        const classified =
          original !== null && original.category_id === row.referenceId;
        return {
          category_id: row.referenceId,
          amount: { minor_units: categoryAmounts[index], currency: "EUR" },
          provenance: classified ? original.provenance : OWNER_PROVENANCE,
          evidence: classified ? original.evidence : null,
          owner_override: classified ? original.owner_override : true,
          receipt_line_id: original?.receipt_line_id ?? null,
        };
      }),
      payment_allocations: editor.paymentAllocations.map((row, index) => ({
        funding_source_id: row.referenceId,
        amount: { minor_units: paymentAmounts[index], currency: "EUR" },
      })),
      original_expense_id: null,
      receipt_id: editing?.receipt_id ?? null,
    };
    setFormErrors([]);
    save.mutate(input);
  }

  function beginCorrection(transaction: Transaction) {
    setEditing(transaction);
    setEditor({
      amount: minorUnitsToEuroInput(transaction.amount.minor_units),
      occurredAt: romeDateTimeInput(new Date(transaction.occurred_at)),
      description: transaction.description,
      merchant: transaction.merchant ?? "",
      categoryAllocations: transaction.category_allocations.map((item) =>
        categoryAllocationRow(
          item.category_id,
          minorUnitsToEuroInput(item.amount.minor_units),
          item,
        ),
      ),
      paymentAllocations: transaction.payment_allocations.map((item) =>
        allocationRow(
          item.funding_source_id,
          minorUnitsToEuroInput(item.amount.minor_units),
        ),
      ),
    });
    setFormErrors([]);
    document.getElementById("movement-editor")?.scrollIntoView();
  }

  const operationError = save.error ?? action.error;
  return (
    <main className="page" id="main-content">
      <Group align="end" className="page-heading" justify="space-between">
        <div>
          <Text className="eyebrow">Registro verificabile</Text>
          <Title order={1}>Movimenti</Title>
          <Text c="dimmed" mt="xs">
            Bozze, registrazioni e annullamenti restano visibili senza
            cancellazioni.
          </Text>
        </div>
        <label className="month-field">
          <span>Mese da osservare</span>
          <input
            onChange={(event) => setPeriod(event.currentTarget.value)}
            type="month"
            value={period}
          />
        </label>
      </Group>

      <Card className="editor-card" id="movement-editor" radius="lg">
        <form onSubmit={submit}>
          <Stack gap="md">
            <Group justify="space-between">
              <div>
                <Text className="eyebrow">
                  {editing === null ? "Inserimento manuale" : "Correzione"}
                </Text>
                <Title order={2}>
                  {editing === null ? "Nuovo movimento" : editing.description}
                </Title>
              </div>
              {editing !== null ? (
                <Button
                  onClick={() => {
                    setEditing(null);
                    setEditor(emptyEditor());
                    setFormErrors([]);
                  }}
                  type="button"
                  variant="subtle"
                >
                  Chiudi correzione
                </Button>
              ) : null}
            </Group>
            <SimpleGrid cols={{ base: 1, sm: 2 }}>
              <TextInput
                label="Importo in euro"
                onChange={(event) => update("amount", event.currentTarget.value)}
                placeholder="12,50 oppure -12,50"
                required
                value={editor.amount}
              />
              <TextInput
                label="Data e ora"
                onChange={(event) =>
                  update("occurredAt", event.currentTarget.value)
                }
                required
                type="datetime-local"
                value={editor.occurredAt}
              />
              <TextInput
                label="Descrizione"
                onChange={(event) =>
                  update("description", event.currentTarget.value)
                }
                required
                value={editor.description}
              />
              <TextInput
                label="Esercente, facoltativo"
                onChange={(event) => update("merchant", event.currentTarget.value)}
                value={editor.merchant}
              />
            </SimpleGrid>
            <AllocationFieldset
              dimension="category"
              onAdd={() =>
                setEditor((current) => ({
                  ...current,
                  categoryAllocations: [
                    ...current.categoryAllocations,
                    categoryAllocationRow(),
                  ],
                }))
              }
              onChange={updateCategoryRow}
              onRemove={(key) =>
                setEditor((current) => ({
                  ...current,
                  categoryAllocations: current.categoryAllocations.filter(
                    (row) => row.key !== key,
                  ),
                }))
              }
              references={register.data?.categories ?? []}
              rows={editor.categoryAllocations}
              total={editor.amount}
            />
            <AllocationFieldset
              dimension="payment"
              onAdd={() =>
                setEditor((current) => ({
                  ...current,
                  paymentAllocations: [
                    ...current.paymentAllocations,
                    allocationRow(),
                  ],
                }))
              }
              onChange={updatePaymentRow}
              onRemove={(key) =>
                setEditor((current) => ({
                  ...current,
                  paymentAllocations: current.paymentAllocations.filter(
                    (row) => row.key !== key,
                  ),
                }))
              }
              references={register.data?.funding_sources ?? []}
              rows={editor.paymentAllocations}
              total={editor.amount}
            />
            {formErrors.length > 0 ? (
              <Alert color="red" role="alert" title="Correggi il movimento">
                <ul className="form-errors">
                  {formErrors.map((error) => (
                    <li key={error}>{error}</li>
                  ))}
                </ul>
              </Alert>
            ) : null}
            <Button loading={save.isPending} type="submit">
              {editing === null ? "Salva come bozza" : "Salva correzione"}
            </Button>
          </Stack>
        </form>
      </Card>

      {operationError ? (
        <Alert color="red" mt="xl" role="alert" title="Operazione non completata">
          {operationError.message}
        </Alert>
      ) : null}
      {register.isPending ? (
        <Text aria-live="polite" mt="xl">
          Caricamento dei movimenti…
        </Text>
      ) : null}
      {register.isError ? (
        <Alert color="red" mt="xl" role="alert" title="Registro non disponibile">
          <Stack gap="sm">
            <Text>{register.error.message}</Text>
            <Button onClick={() => void register.refetch()} variant="light">
              Riprova
            </Button>
          </Stack>
        </Alert>
      ) : null}
      {register.data ? (
        <TransactionTable
          busy={action.isPending}
          onAction={(transaction, kind) => {
            if (
              kind === "cancel" &&
              !window.confirm(
                transaction.status === "posted"
                  ? "Annullare il movimento registrato?"
                  : "Scartare questa bozza?",
              )
            ) {
              return;
            }
            action.mutate({ transaction, kind });
          }}
          onCorrect={beginCorrection}
          register={register.data}
        />
      ) : null}
    </main>
  );
}

function AllocationFieldset({
  dimension,
  onAdd,
  onChange,
  onRemove,
  references,
  rows,
  total,
}: {
  dimension: "category" | "payment";
  onAdd: () => void;
  onChange: (
    key: number,
    field: "referenceId" | "amount",
    value: string,
  ) => void;
  onRemove: (key: number) => void;
  references: Array<{
    id: string;
    name: string;
    status: "active" | "archived";
  }>;
  rows: AllocationRow[];
  total: string;
}) {
  const category = dimension === "category";
  const labels = category
    ? {
        legend: "Ripartizione per categoria",
        reference: "Categoria",
        amount: "Importo categoria",
        add: "Aggiungi categoria",
        empty: "Seleziona una categoria",
        plural: "categorie",
      }
    : {
        legend: "Ripartizione per fonte",
        reference: "Fonte di pagamento",
        amount: "Importo fonte",
        add: "Aggiungi fonte",
        empty: "Seleziona una fonte",
        plural: "fonti",
      };
  const totalMinorUnits = parseEuroToMinorUnits(total);
  const analysis = analyzeAllocations(rows);
  const reconciled =
    totalMinorUnits !== null && analysis.sum === BigInt(totalMinorUnits);
  let summary = `Somma ${labels.plural}: completa tutti gli importi.`;
  if (analysis.sum !== null && totalMinorUnits !== null) {
    const difference = BigInt(totalMinorUnits) - analysis.sum;
    summary = reconciled
      ? `Somma ${labels.plural}: ${formatExactEuro(analysis.sum)}. Riconciliata con il totale.`
      : `Somma ${labels.plural}: ${formatExactEuro(analysis.sum)}. Differenza rispetto al totale: ${formatExactEuro(difference)}.`;
  }

  return (
    <Fieldset className="allocation-fieldset" legend={labels.legend}>
      <Stack gap="sm">
        {rows.map((row, index) => {
          const selected = references.find(
            (reference) => reference.id === row.referenceId,
          );
          const historical =
            row.referenceId.length > 0 && selected?.status !== "active";
          const warningId = `${dimension}-allocation-${row.key}-warning`;
          const options: Array<{
            label: string;
            value: string;
            disabled?: boolean;
          }> = [
            { label: labels.empty, value: "" },
            ...references
              .filter((reference) => reference.status === "active")
              .map((reference) => ({
                label: reference.name,
                value: reference.id,
              })),
          ];
          if (historical) {
            options.splice(1, 0, {
              label: `${selected?.name ?? "Riferimento non disponibile"} (archiviata: solo storico)`,
              value: row.referenceId,
              disabled: true,
            });
          }
          return (
            <Paper className="allocation-row" key={row.key} p="sm" radius="md">
              <Stack gap="xs">
                <SimpleGrid cols={{ base: 1, sm: 2 }}>
                  <NativeSelect
                    aria-describedby={historical ? warningId : undefined}
                    data={options}
                    label={`${labels.reference} ${index + 1}`}
                    onChange={(event) =>
                      onChange(row.key, "referenceId", event.currentTarget.value)
                    }
                    required
                    value={row.referenceId}
                  />
                  <TextInput
                    label={`${labels.amount} ${index + 1} in euro`}
                    onChange={(event) =>
                      onChange(row.key, "amount", event.currentTarget.value)
                    }
                    placeholder="12,50 oppure -12,50"
                    required
                    value={row.amount}
                  />
                </SimpleGrid>
                {historical ? (
                  <Text c="orange.9" id={warningId} size="sm">
                    {selected?.name ?? "Questo riferimento"} è disponibile solo
                    per la storia. Scegli un riferimento attivo per una nuova
                    allocazione.
                  </Text>
                ) : null}
                {rows.length > 1 ? (
                  <Button
                    style={{ alignSelf: "flex-start" }}
                    color="red"
                    onClick={() => onRemove(row.key)}
                    size="compact-sm"
                    type="button"
                    variant="subtle"
                  >
                    Rimuovi {category ? "categoria" : "fonte"} {index + 1}
                  </Button>
                ) : null}
              </Stack>
            </Paper>
          );
        })}
        <Group align="center" justify="space-between">
          <Text
            aria-live="polite"
            c={reconciled ? "teal.8" : "dimmed"}
            data-reconciled={reconciled}
            size="sm"
          >
            {summary}
          </Text>
          <Button onClick={onAdd} type="button" variant="light">
            {labels.add}
          </Button>
        </Group>
      </Stack>
    </Fieldset>
  );
}

function TransactionTable({
  busy,
  onAction,
  onCorrect,
  register,
}: {
  busy: boolean;
  onAction: (transaction: Transaction, kind: "post" | "cancel") => void;
  onCorrect: (transaction: Transaction) => void;
  register: TransactionRegister;
}) {
  if (register.transactions.length === 0) {
    return (
      <Paper className="empty-state" mt="xl" radius="lg">
        <Text fw={700}>Nessun movimento in questo mese.</Text>
        <Text c="dimmed">Puoi iniziare salvando una bozza dal modulo sopra.</Text>
      </Paper>
    );
  }
  return (
    <Card className="table-card transaction-table" mt="xl" radius="lg">
      <AccessibleTableScrollContainer
        ariaLabel="Registro mensile dei movimenti"
        minWidth={840}
      >
        <Table highlightOnHover>
          <Table.Caption>
            Registro completo del mese, inclusi stati che non entrano nei totali.
          </Table.Caption>
          <Table.Thead>
            <Table.Tr>
              <Table.Th scope="col">Movimento</Table.Th>
              <Table.Th scope="col">Categoria</Table.Th>
              <Table.Th scope="col">Fonte</Table.Th>
              <Table.Th scope="col">Importo</Table.Th>
              <Table.Th scope="col">Stato</Table.Th>
              <Table.Th scope="col">Azioni</Table.Th>
            </Table.Tr>
          </Table.Thead>
          <Table.Tbody>
            {register.transactions.map((transaction) => (
              <Table.Tr key={transaction.id}>
                <Table.Td>
                  <Text fw={700}>{transaction.description}</Text>
                  <Text c="dimmed" size="sm">
                    {formatOccurredAt(transaction.occurred_at)}
                    {transaction.merchant ? ` · ${transaction.merchant}` : ""}
                  </Text>
                </Table.Td>
                <Table.Td>
                  {transaction.category_allocations
                    .map((item) => item.category_name ?? "Categoria non disponibile")
                    .join(", ")}
                </Table.Td>
                <Table.Td>
                  {transaction.payment_allocations
                    .map(
                      (item) =>
                        item.funding_source_name ?? "Fonte non disponibile",
                    )
                    .join(", ")}
                </Table.Td>
                <Table.Td>{formatMoney(transaction.amount)}</Table.Td>
                <Table.Td>
                  <Badge color={statusColors[transaction.status]} variant="light">
                    {statusLabels[transaction.status]}
                  </Badge>
                </Table.Td>
                <Table.Td>
                  <Group gap="xs" wrap="nowrap">
                    {transaction.status === "draft" ||
                    transaction.status === "needs_review" ? (
                      <Button
                        disabled={busy}
                        onClick={() => onAction(transaction, "post")}
                        size="xs"
                        variant="light"
                      >
                        Registra
                      </Button>
                    ) : null}
                    {transaction.status !== "voided" &&
                    transaction.status !== "discarded" ? (
                      <>
                        <Button
                          disabled={busy}
                          onClick={() => onCorrect(transaction)}
                          size="xs"
                          variant="subtle"
                        >
                          Correggi
                        </Button>
                        <Button
                          color="red"
                          disabled={busy}
                          onClick={() => onAction(transaction, "cancel")}
                          size="xs"
                          variant="subtle"
                        >
                          {transaction.status === "posted"
                            ? "Annulla"
                            : "Scarta"}
                        </Button>
                      </>
                    ) : null}
                  </Group>
                </Table.Td>
              </Table.Tr>
            ))}
          </Table.Tbody>
        </Table>
      </AccessibleTableScrollContainer>
    </Card>
  );
}
