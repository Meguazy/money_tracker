import {
  Alert,
  Badge,
  Button,
  Card,
  Group,
  Paper,
  SimpleGrid,
  Skeleton,
  Stack,
  Table,
  Text,
  Title,
} from "@mantine/core";
import { useQuery } from "@tanstack/react-query";
import { useState } from "react";

import type { MonthlyOverview } from "./api";
import { loadMonthlyOverview } from "./api";
import { BudgetChart } from "./BudgetChart";
import { currentRomeMonth, formatMoney, monthLabel } from "./format";

type OverviewLoader = (
  period: string,
  signal?: AbortSignal,
) => Promise<MonthlyOverview>;

type OverviewPageProps = {
  loader?: OverviewLoader;
};

export function OverviewPage({ loader = loadMonthlyOverview }: OverviewPageProps) {
  const [period, setPeriod] = useState(currentRomeMonth);
  const query = useQuery({
    queryKey: ["monthly-overview", period],
    queryFn: ({ signal }) => loader(period, signal),
    retry: false,
  });

  return (
    <main className="page" id="main-content">
      <Group align="end" className="page-heading" justify="space-between">
        <div>
          <Text className="eyebrow">Quadro mensile</Text>
          <Title order={1}>Dove stanno andando i tuoi soldi</Title>
          <Text c="dimmed" mt="xs">
            Solo movimenti registrati, nel calendario Europe/Rome.
          </Text>
        </div>
        <label className="month-field">
          <span>Mese da osservare</span>
          <input
            max="9999-12"
            min="0001-01"
            onChange={(event) => setPeriod(event.currentTarget.value)}
            type="month"
            value={period}
          />
        </label>
      </Group>

      {query.isPending ? <OverviewLoading /> : null}
      {query.isError ? (
        <Alert color="red" role="alert" title="Dati non disponibili">
          <Stack gap="sm">
            <Text>{query.error.message}</Text>
            <Button onClick={() => void query.refetch()} variant="light">
              Riprova
            </Button>
          </Stack>
        </Alert>
      ) : null}
      {query.data ? <OverviewContent overview={query.data} period={period} /> : null}
    </main>
  );
}

function OverviewLoading() {
  return (
    <Stack aria-live="polite" gap="lg">
      <Text>Caricamento del riepilogo…</Text>
      <SimpleGrid cols={{ base: 1, sm: 3 }}>
        <Skeleton height={150} radius="lg" />
        <Skeleton height={150} radius="lg" />
        <Skeleton height={150} radius="lg" />
      </SimpleGrid>
    </Stack>
  );
}

function OverviewContent({
  overview,
  period,
}: {
  overview: MonthlyOverview;
  period: string;
}) {
  const noActivity = overview.posted_total.minor_units === 0;
  return (
    <Stack gap="xl">
      <SimpleGrid cols={{ base: 1, sm: 3 }}>
        <MetricCard
          accent="coral"
          label={`Spesa netta · ${monthLabel(period)}`}
          value={formatMoney(overview.posted_total)}
        />
        <MetricCard
          accent="ink"
          label="Fondi personali"
          value={formatMoney(overview.funding_split.personal_funds)}
        />
        <MetricCard
          accent="sage"
          label="Benefit"
          value={formatMoney(overview.funding_split.benefit)}
        />
      </SimpleGrid>

      {noActivity ? (
        <Paper className="empty-state" radius="lg">
          <Text fw={700}>Nessun movimento registrato in questo mese.</Text>
          <Text c="dimmed">Le bozze non entrano nei totali finché non le registri.</Text>
        </Paper>
      ) : null}

      <section aria-labelledby="budget-heading">
        <Group justify="space-between" mb="md">
          <div>
            <Text className="eyebrow">Obiettivi</Text>
            <Title id="budget-heading" order={2}>
              Budget sotto controllo
            </Title>
          </div>
          <Badge color="dark" variant="light">
            {overview.budgets.length} configurati
          </Badge>
        </Group>
        {overview.budgets.length === 0 ? (
          <Paper className="empty-state" radius="lg">
            <Text fw={700}>Non ci sono budget configurati.</Text>
            <Text c="dimmed">Il totale resta disponibile senza inventare un limite.</Text>
          </Paper>
        ) : (
          <SimpleGrid cols={{ base: 1, lg: 2 }} spacing="xl">
            <Card className="chart-card" radius="lg">
              <BudgetChart budgets={overview.budgets} />
            </Card>
            <BudgetTable overview={overview} />
          </SimpleGrid>
        )}
      </section>

      {overview.unbudgeted_categories.length > 0 ? (
        <Alert color="orange" title="Spesa senza un budget applicabile">
          {overview.unbudgeted_categories.join(", ")}
        </Alert>
      ) : null}
    </Stack>
  );
}

function MetricCard({
  accent,
  label,
  value,
}: {
  accent: "coral" | "ink" | "sage";
  label: string;
  value: string;
}) {
  return (
    <Card className={`metric metric--${accent}`} radius="lg">
      <Text className="metric__label">{label}</Text>
      <Text className="metric__value">{value}</Text>
    </Card>
  );
}

function BudgetTable({ overview }: { overview: MonthlyOverview }) {
  return (
    <Card className="table-card" radius="lg">
      <Table.ScrollContainer minWidth={560}>
        <Table striped highlightOnHover>
          <Table.Caption>
            Alternativa tabellare al grafico: spesa, obiettivo e residuo esatti.
          </Table.Caption>
          <Table.Thead>
            <Table.Tr>
              <Table.Th scope="col">Categoria</Table.Th>
              <Table.Th scope="col">Spesa</Table.Th>
              <Table.Th scope="col">Obiettivo</Table.Th>
              <Table.Th scope="col">Residuo</Table.Th>
            </Table.Tr>
          </Table.Thead>
          <Table.Tbody>
            {overview.budgets.map((budget) => (
              <Table.Tr key={budget.category_name}>
                <Table.Th scope="row">{budget.category_name}</Table.Th>
                <Table.Td>{formatMoney(budget.posted_spend)}</Table.Td>
                <Table.Td>{formatMoney(budget.target)}</Table.Td>
                <Table.Td>
                  <Badge
                    color={budget.remaining.minor_units < 0 ? "red" : "teal"}
                    variant="light"
                  >
                    {formatMoney(budget.remaining)}
                  </Badge>
                </Table.Td>
              </Table.Tr>
            ))}
          </Table.Tbody>
        </Table>
      </Table.ScrollContainer>
    </Card>
  );
}
