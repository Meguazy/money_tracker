import {
  Alert,
  Badge,
  Button,
  Card,
  Fieldset,
  Group,
  NativeSelect,
  Paper,
  Progress,
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
  type BudgetInput,
  type BudgetPlan,
  type BudgetPlanEntry,
  createBudget,
  loadBudgetPlan,
  replaceBudget,
} from "./api";
import { AccessibleTableScrollContainer } from "./AccessibleTableScrollContainer";
import {
  currentRomeMonth,
  formatMoney,
  minorUnitsToEuroInput,
  monthLabel,
  parseEuroToMinorUnits,
} from "./format";

type BudgetApis = {
  load: typeof loadBudgetPlan;
  create: typeof createBudget;
  replace: typeof replaceBudget;
};

type OverrideRow = {
  key: number;
  period: string;
  target: string;
};

type EditorState = {
  categoryId: string;
  recurringTarget: string;
  overrides: OverrideRow[];
};

const defaultApis: BudgetApis = {
  load: loadBudgetPlan,
  create: createBudget,
  replace: replaceBudget,
};

let nextOverrideKey = 0;

function overrideRow(period = "", target = ""): OverrideRow {
  nextOverrideKey += 1;
  return { key: nextOverrideKey, period, target };
}

function emptyEditor(): EditorState {
  return { categoryId: "", recurringTarget: "", overrides: [] };
}

function periodText(period: { year: number; month: number }): string {
  return `${period.year}-${String(period.month).padStart(2, "0")}`;
}

export function BudgetsPage({ apis = defaultApis }: { apis?: BudgetApis }) {
  const queryClient = useQueryClient();
  const [period, setPeriod] = useState(currentRomeMonth);
  const [editor, setEditor] = useState<EditorState>(emptyEditor);
  const [editing, setEditing] = useState<BudgetPlanEntry | null>(null);
  const [formErrors, setFormErrors] = useState<string[]>([]);
  const plan = useQuery({
    queryKey: ["budget-plan", period],
    queryFn: ({ signal }) => apis.load(period, signal),
    retry: false,
  });
  const save = useMutation({
    mutationFn: async (input: BudgetInput & { categoryId: string }) => {
      const { categoryId, ...plan } = input;
      return editing === null
        ? apis.create(categoryId, plan)
        : apis.replace(categoryId, {
            ...plan,
            expected_version: editing.version,
          });
    },
    onSuccess: async () => {
      setEditor(emptyEditor());
      setEditing(null);
      setFormErrors([]);
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["budget-plan"] }),
        queryClient.invalidateQueries({ queryKey: ["monthly-overview"] }),
        queryClient.invalidateQueries({ queryKey: ["range-overview"] }),
      ]);
    },
  });

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const errors: string[] = [];
    if (editing === null && editor.categoryId.length === 0) {
      errors.push("Scegli la categoria da mettere a budget.");
    }
    const recurring = parseEuroToMinorUnits(editor.recurringTarget);
    if (recurring === null || recurring <= 0) {
      errors.push("Il budget mensile deve essere un importo positivo.");
    }
    const overrides = editor.overrides.map((row) => ({
      period: row.period,
      target: parseEuroToMinorUnits(row.target),
    }));
    if (overrides.some((item) => item.period.length === 0)) {
      errors.push("Indica il mese di ogni eccezione.");
    }
    if (overrides.some((item) => item.target === null || item.target <= 0)) {
      errors.push("Ogni eccezione deve avere un importo positivo.");
    }
    const months = overrides
      .map((item) => item.period)
      .filter((month) => month.length > 0);
    if (new Set(months).size !== months.length) {
      errors.push("Ogni mese può avere una sola eccezione.");
    }
    if (errors.length > 0 || recurring === null) {
      setFormErrors(errors);
      return;
    }
    setFormErrors([]);
    save.mutate({
      categoryId: editing?.category_id ?? editor.categoryId,
      recurring_target: { minor_units: recurring, currency: "EUR" },
      overrides: overrides.map((item) => {
        const [year, month] = item.period.split("-").map(Number);
        return {
          period: { year, month },
          target: { minor_units: item.target as number, currency: "EUR" },
        };
      }),
    });
  }

  function beginCorrection(entry: BudgetPlanEntry) {
    setEditing(entry);
    setEditor({
      categoryId: entry.category_id,
      recurringTarget: minorUnitsToEuroInput(entry.recurring_target.minor_units),
      overrides: entry.overrides.map((item) =>
        overrideRow(
          periodText(item.period),
          minorUnitsToEuroInput(item.target.minor_units),
        ),
      ),
    });
    setFormErrors([]);
    document.getElementById("budget-editor")?.scrollIntoView();
  }

  return (
    <main className="page" id="main-content">
      <Group align="end" className="page-heading" justify="space-between">
        <div>
          <Text className="eyebrow">Budget mensili</Text>
          <Title order={1}>Quanto vuoi spendere</Title>
          <Text c="dimmed" mt="xs">
            Nessun riporto tra i mesi: ogni mese parte dal suo obiettivo.
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

      <Card className="editor-card" id="budget-editor" radius="lg">
        <form onSubmit={submit}>
          <Stack gap="md">
            <Group justify="space-between">
              <div>
                <Text className="eyebrow">
                  {editing === null ? "Nuovo budget" : "Correzione"}
                </Text>
                <Title order={2}>
                  {editing === null
                    ? "Metti a budget una categoria"
                    : editing.category_name}
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
              {editing === null ? (
                <NativeSelect
                  data={[
                    { label: "Seleziona una categoria", value: "" },
                    ...(plan.data?.budgetable_categories ?? []).map(
                      (category) => ({
                        label: category.name,
                        value: category.id,
                      }),
                    ),
                  ]}
                  label="Categoria"
                  onChange={(event) => {
                    const categoryId = event.currentTarget.value;
                    setEditor((current) => ({
                      ...current,
                      categoryId,
                    }));
                  }}
                  required
                  value={editor.categoryId}
                />
              ) : null}
              <TextInput
                label="Budget mensile in euro"
                onChange={(event) => {
                  const recurringTarget = event.currentTarget.value;
                  setEditor((current) => ({
                    ...current,
                    recurringTarget,
                  }));
                }}
                placeholder="400,00"
                required
                value={editor.recurringTarget}
              />
            </SimpleGrid>
            <Fieldset
              className="allocation-fieldset"
              legend="Eccezioni per un mese"
            >
              <Stack gap="sm">
                {editor.overrides.length === 0 ? (
                  <Text c="dimmed" size="sm">
                    Nessuna eccezione: ogni mese usa il budget ricorrente.
                  </Text>
                ) : null}
                {editor.overrides.map((row, index) => (
                  <Paper className="allocation-row" key={row.key} p="sm" radius="md">
                    <Stack gap="xs">
                      <SimpleGrid cols={{ base: 1, sm: 2 }}>
                        <TextInput
                          label={`Mese eccezione ${index + 1}`}
                          onChange={(event) => {
                            const period = event.currentTarget.value;
                            setEditor((current) => ({
                              ...current,
                              overrides: current.overrides.map((item) =>
                                item.key === row.key
                                  ? { ...item, period }
                                  : item,
                              ),
                            }));
                          }}
                          required
                          type="month"
                          value={row.period}
                        />
                        <TextInput
                          label={`Budget eccezione ${index + 1} in euro`}
                          onChange={(event) => {
                            const target = event.currentTarget.value;
                            setEditor((current) => ({
                              ...current,
                              overrides: current.overrides.map((item) =>
                                item.key === row.key
                                  ? { ...item, target }
                                  : item,
                              ),
                            }));
                          }}
                          placeholder="600,00"
                          required
                          value={row.target}
                        />
                      </SimpleGrid>
                      <Button
                        color="red"
                        onClick={() =>
                          setEditor((current) => ({
                            ...current,
                            overrides: current.overrides.filter(
                              (item) => item.key !== row.key,
                            ),
                          }))
                        }
                        size="compact-sm"
                        style={{ alignSelf: "flex-start" }}
                        type="button"
                        variant="subtle"
                      >
                        Rimuovi eccezione {index + 1}
                      </Button>
                    </Stack>
                  </Paper>
                ))}
                <Button
                  onClick={() =>
                    setEditor((current) => ({
                      ...current,
                      overrides: [...current.overrides, overrideRow()],
                    }))
                  }
                  style={{ alignSelf: "flex-start" }}
                  type="button"
                  variant="light"
                >
                  Aggiungi eccezione
                </Button>
              </Stack>
            </Fieldset>
            {formErrors.length > 0 ? (
              <Alert color="red" role="alert" title="Correggi il budget">
                <ul className="form-errors">
                  {formErrors.map((error) => (
                    <li key={error}>{error}</li>
                  ))}
                </ul>
              </Alert>
            ) : null}
            <Button loading={save.isPending} type="submit">
              {editing === null ? "Salva budget" : "Salva correzione"}
            </Button>
          </Stack>
        </form>
      </Card>

      {save.error ? (
        <Alert color="red" mt="xl" role="alert" title="Operazione non completata">
          {save.error.message}
        </Alert>
      ) : null}
      {plan.isPending ? (
        <Text aria-live="polite" mt="xl">
          Caricamento dei budget…
        </Text>
      ) : null}
      {plan.isError ? (
        <Alert color="red" mt="xl" role="alert" title="Budget non disponibili">
          <Stack gap="sm">
            <Text>{plan.error.message}</Text>
            <Button onClick={() => void plan.refetch()} variant="light">
              Riprova
            </Button>
          </Stack>
        </Alert>
      ) : null}
      {plan.data ? (
        <BudgetTable onCorrect={beginCorrection} period={period} plan={plan.data} />
      ) : null}
    </main>
  );
}

function BudgetTable({
  onCorrect,
  period,
  plan,
}: {
  onCorrect: (entry: BudgetPlanEntry) => void;
  period: string;
  plan: BudgetPlan;
}) {
  if (plan.entries.length === 0) {
    return (
      <Paper className="empty-state" mt="xl" radius="lg">
        <Text fw={700}>Nessun budget configurato.</Text>
        <Text c="dimmed">
          Scegli una categoria nel modulo sopra per iniziare a misurare un
          obiettivo mensile.
        </Text>
      </Paper>
    );
  }
  return (
    <Card className="table-card" mt="xl" radius="lg">
      <AccessibleTableScrollContainer
        ariaLabel="Piano dei budget mensili"
        minWidth={720}
      >
        <Table highlightOnHover>
          <Table.Caption>
            Budget di {monthLabel(period)}, dal più sforato al più libero.
          </Table.Caption>
          <Table.Thead>
            <Table.Tr>
              <Table.Th scope="col">Categoria</Table.Th>
              <Table.Th scope="col">Obiettivo</Table.Th>
              <Table.Th scope="col">Speso</Table.Th>
              <Table.Th scope="col">Residuo</Table.Th>
              <Table.Th scope="col">Azioni</Table.Th>
            </Table.Tr>
          </Table.Thead>
          <Table.Tbody>
            {plan.entries.map((entry) => {
              const overspent = entry.remaining.minor_units < 0;
              const used =
                entry.target.minor_units > 0
                  ? Math.min(
                      100,
                      Math.round(
                        (entry.posted_spend.minor_units * 100) /
                          entry.target.minor_units,
                      ),
                    )
                  : 0;
              return (
                <Table.Tr key={entry.category_id}>
                  <Table.Td>
                    <Text fw={700}>{entry.category_name}</Text>
                    {entry.overrides.length > 0 ? (
                      <Text c="dimmed" size="sm">
                        {entry.overrides.length} eccezione/i configurate
                      </Text>
                    ) : null}
                  </Table.Td>
                  <Table.Td>
                    <Text>{formatMoney(entry.target)}</Text>
                    {entry.target.minor_units !==
                    entry.recurring_target.minor_units ? (
                      <Text c="dimmed" size="sm">
                        Ricorrente {formatMoney(entry.recurring_target)}
                      </Text>
                    ) : null}
                  </Table.Td>
                  <Table.Td>
                    <Text>{formatMoney(entry.posted_spend)}</Text>
                    <Progress
                      aria-hidden="true"
                      color={overspent ? "red" : "teal"}
                      mt="xs"
                      value={used}
                    />
                  </Table.Td>
                  <Table.Td>
                    <Badge color={overspent ? "red" : "teal"} variant="light">
                      {formatMoney(entry.remaining)}
                    </Badge>
                  </Table.Td>
                  <Table.Td>
                    <Button
                      onClick={() => onCorrect(entry)}
                      size="xs"
                      variant="subtle"
                    >
                      Correggi
                    </Button>
                  </Table.Td>
                </Table.Tr>
              );
            })}
          </Table.Tbody>
        </Table>
      </AccessibleTableScrollContainer>
    </Card>
  );
}
