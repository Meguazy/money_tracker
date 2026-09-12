import {
  Alert,
  Badge,
  Button,
  Card,
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
import {
  currentLocalDateTime,
  currentRomeMonth,
  formatMoney,
  formatOccurredAt,
  parseEuroToMinorUnits,
} from "./format";

type TransactionApis = {
  load: typeof loadTransactions;
  create: typeof createExpense;
  replace: typeof replaceExpense;
  post: typeof postExpense;
  cancel: typeof cancelExpense;
};

type EditorState = {
  amount: string;
  occurredAt: string;
  description: string;
  merchant: string;
  categoryId: string;
  fundingSourceId: string;
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

function emptyEditor(): EditorState {
  return {
    amount: "",
    occurredAt: currentLocalDateTime(),
    description: "",
    merchant: "",
    categoryId: "",
    fundingSourceId: "",
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
  const [formError, setFormError] = useState<string | null>(null);
  const register = useQuery({
    queryKey: ["transactions", period],
    queryFn: ({ signal }) => apis.load(period, signal),
    retry: false,
  });
  const refresh = async () => {
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: ["transactions"] }),
      queryClient.invalidateQueries({ queryKey: ["monthly-overview"] }),
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
      setFormError(null);
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

  function update(field: keyof EditorState, value: string | null) {
    setEditor((current) => ({ ...current, [field]: value ?? "" }));
  }

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const minorUnits = parseEuroToMinorUnits(editor.amount);
    if (
      minorUnits === null ||
      editor.description.trim().length === 0 ||
      editor.occurredAt.length === 0 ||
      editor.categoryId.length === 0 ||
      editor.fundingSourceId.length === 0
    ) {
      setFormError("Completa importo, data, descrizione, categoria e fonte.");
      return;
    }
    const input: ExpenseInput = {
      amount: { minor_units: minorUnits, currency: "EUR" },
      occurred_at: new Date(editor.occurredAt).toISOString(),
      description: editor.description.trim(),
      merchant: editor.merchant.trim() || null,
      category_allocations: [
        {
          category_id: editor.categoryId,
          amount: { minor_units: minorUnits, currency: "EUR" },
          provenance: "owner:web:v1",
          owner_override: true,
        },
      ],
      payment_allocations: [
        {
          funding_source_id: editor.fundingSourceId,
          amount: { minor_units: minorUnits, currency: "EUR" },
        },
      ],
      original_expense_id: null,
      receipt_id: editing?.receipt_id ?? null,
    };
    setFormError(null);
    save.mutate(input);
  }

  function beginCorrection(transaction: Transaction) {
    if (
      transaction.category_allocations.length !== 1 ||
      transaction.payment_allocations.length !== 1
    ) {
      setFormError(
        "La correzione visuale delle ripartizioni multiple sarà disponibile in una tranche successiva.",
      );
      return;
    }
    setEditing(transaction);
    setEditor({
      amount: (transaction.amount.minor_units / 100)
        .toFixed(2)
        .replace(".", ","),
      occurredAt: currentLocalDateTime(new Date(transaction.occurred_at)),
      description: transaction.description,
      merchant: transaction.merchant ?? "",
      categoryId: transaction.category_allocations[0].category_id,
      fundingSourceId: transaction.payment_allocations[0].funding_source_id,
    });
    setFormError(null);
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
              <NativeSelect
                data={[
                  { label: "Seleziona una categoria", value: "" },
                  ...(register.data?.categories ?? [])
                    .filter((category) => category.status === "active")
                    .map((category) => ({
                      label: category.name,
                      value: category.id,
                    })),
                ]}
                label="Categoria"
                onChange={(event) =>
                  update("categoryId", event.currentTarget.value)
                }
                required
                value={editor.categoryId}
              />
              <NativeSelect
                data={[
                  { label: "Seleziona una fonte", value: "" },
                  ...(register.data?.funding_sources ?? [])
                    .filter((source) => source.status === "active")
                    .map((source) => ({
                      label: source.name,
                      value: source.id,
                    })),
                ]}
                label="Fonte di pagamento"
                onChange={(event) =>
                  update("fundingSourceId", event.currentTarget.value)
                }
                required
                value={editor.fundingSourceId}
              />
            </SimpleGrid>
            {formError !== null ? (
              <Alert color="red" role="alert">
                {formError}
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
      <Table.ScrollContainer minWidth={840}>
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
      </Table.ScrollContainer>
    </Card>
  );
}
