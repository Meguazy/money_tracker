import {
  Alert,
  Badge,
  Button,
  Card,
  Group,
  Paper,
  Select,
  SimpleGrid,
  Skeleton,
  Stack,
  Table,
  Text,
  Title,
} from "@mantine/core";
import { useQuery } from "@tanstack/react-query";
import { lazy, Suspense, useState, type FormEvent } from "react";

import type {
  FundingClass,
  PeriodGranularity,
  RangeOverview,
  ResolvedPeriod,
  RangeSelection,
} from "./api";
import { loadRangeOverview } from "./api";
import {
  currentRomeDate,
  currentRomeMonth,
  formatMoney,
  monthLabel,
} from "./format";

type RangeLoader = (
  selection: RangeSelection,
  signal?: AbortSignal,
) => Promise<RangeOverview>;

type AnalyticsPageProps = {
  loader?: RangeLoader;
};

const MerchantChart = lazy(async () => ({
  default: (await import("./AnalyticsCharts")).MerchantChart,
}));
const PeriodComparisonChart = lazy(async () => ({
  default: (await import("./AnalyticsCharts")).PeriodComparisonChart,
}));

const GRANULARITIES = [
  { value: "day", label: "Giorno" },
  { value: "week", label: "Settimana ISO" },
  { value: "month", label: "Mese" },
  { value: "year", label: "Anno" },
  { value: "custom", label: "Intervallo personalizzato" },
];

function initialValues() {
  const day = currentRomeDate();
  const month = currentRomeMonth();
  return {
    day,
    month,
    year: month.slice(0, 4),
    start: month + "-01",
    end: day,
  };
}

export function AnalyticsPage({
  loader = loadRangeOverview,
}: AnalyticsPageProps) {
  const initial = initialValues();
  const [granularity, setGranularity] =
    useState<PeriodGranularity>("month");
  const [day, setDay] = useState(initial.day);
  const [month, setMonth] = useState(initial.month);
  const [year, setYear] = useState(initial.year);
  const [start, setStart] = useState(initial.start);
  const [end, setEnd] = useState(initial.end);
  const [selection, setSelection] = useState<RangeSelection>({
    granularity: "month",
    month: initial.month,
  });
  const query = useQuery({
    queryKey: ["range-overview", selection],
    queryFn: ({ signal }) => loader(selection, signal),
    retry: false,
  });

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (granularity === "day" || granularity === "week") {
      setSelection({ granularity, day });
    } else if (granularity === "month") {
      setSelection({ granularity, month });
    } else if (granularity === "year") {
      setSelection({ granularity, year });
    } else {
      setSelection({ granularity, start, end });
    }
  }

  return (
    <main className="page" id="main-content">
      <div className="page-heading">
        <Text className="eyebrow">Analisi deterministica</Text>
        <Title order={1}>Guarda la spesa nel tempo</Title>
        <Text c="dimmed" mt="xs">
          Giorni e settimane seguono il calendario Europe/Rome. Entrano solo i
          movimenti registrati.
        </Text>
      </div>

      <Paper
        className="analysis-controls"
        component="form"
        onSubmit={submit}
        radius="lg"
      >
        <Select
          data={GRANULARITIES}
          label="Periodo da osservare"
          onChange={(value) =>
            setGranularity((value ?? "month") as PeriodGranularity)
          }
          value={granularity}
        />
        <PeriodFields
          day={day}
          end={end}
          granularity={granularity}
          month={month}
          setDay={setDay}
          setEnd={setEnd}
          setMonth={setMonth}
          setStart={setStart}
          setYear={setYear}
          start={start}
          year={year}
        />
        <Button type="submit">Aggiorna analisi</Button>
      </Paper>

      {query.isPending ? <AnalyticsLoading /> : null}
      {query.isError ? (
        <Alert
          color="red"
          mt="xl"
          role="alert"
          title="Analisi non disponibile"
        >
          <Stack gap="sm">
            <Text>{query.error.message}</Text>
            <Button onClick={() => void query.refetch()} variant="light">
              Riprova
            </Button>
          </Stack>
        </Alert>
      ) : null}
      {query.data ? <AnalyticsContent overview={query.data} /> : null}
    </main>
  );
}

type PeriodFieldsProps = ReturnType<typeof initialValues> & {
  granularity: PeriodGranularity;
  setDay: (value: string) => void;
  setMonth: (value: string) => void;
  setYear: (value: string) => void;
  setStart: (value: string) => void;
  setEnd: (value: string) => void;
};

function PeriodFields(props: PeriodFieldsProps) {
  if (props.granularity === "day" || props.granularity === "week") {
    return (
      <label className="period-field">
        <span>
          {props.granularity === "day"
            ? "Giorno"
            : "Un giorno della settimana"}
        </span>
        <input
          max="9998-12-31"
          min="0001-01-01"
          onChange={(event) => props.setDay(event.currentTarget.value)}
          required
          type="date"
          value={props.day}
        />
      </label>
    );
  }
  if (props.granularity === "month") {
    return (
      <label className="period-field">
        <span>Mese</span>
        <input
          max="9998-12"
          min="0001-01"
          onChange={(event) => props.setMonth(event.currentTarget.value)}
          required
          type="month"
          value={props.month}
        />
      </label>
    );
  }
  if (props.granularity === "year") {
    return (
      <label className="period-field">
        <span>Anno</span>
        <input
          inputMode="numeric"
          max="9998"
          min="1"
          onChange={(event) => props.setYear(event.currentTarget.value)}
          pattern="[0-9]{4}"
          required
          type="text"
          value={props.year}
        />
      </label>
    );
  }
  return (
    <Group className="custom-period" grow>
      <label className="period-field">
        <span>Dal giorno</span>
        <input
          max="9998-12-31"
          min="0001-01-01"
          onChange={(event) => props.setStart(event.currentTarget.value)}
          required
          type="date"
          value={props.start}
        />
      </label>
      <label className="period-field">
        <span>Al giorno</span>
        <input
          max="9998-12-31"
          min="0001-01-01"
          onChange={(event) => props.setEnd(event.currentTarget.value)}
          required
          type="date"
          value={props.end}
        />
      </label>
    </Group>
  );
}

function AnalyticsLoading() {
  return (
    <Stack aria-live="polite" gap="lg" mt="xl">
      <Text>Caricamento dell’analisi…</Text>
      <SimpleGrid cols={{ base: 1, sm: 4 }}>
        {[0, 1, 2, 3].map((item) => (
          <Skeleton height={136} key={item} radius="lg" />
        ))}
      </SimpleGrid>
    </Stack>
  );
}

function AnalyticsContent({ overview }: { overview: RangeOverview }) {
  return (
    <Stack gap="xl" mt="xl">
      <Group align="center" justify="space-between">
        <div>
          <Text className="eyebrow">Periodo risolto</Text>
          <Title order={2}>{periodTitle(overview)}</Title>
          <Text c="dimmed">{periodDates(overview)}</Text>
        </div>
        <Badge color="dark" variant="light">
          {overview.period.day_count}{" "}
          {overview.period.day_count === 1 ? "giorno" : "giorni"}
        </Badge>
      </Group>

      <SimpleGrid cols={{ base: 1, sm: 2, lg: 4 }}>
        <Metric
          label="Spesa netta"
          value={formatMoney(overview.posted_total)}
        />
        <Metric
          label="Movimenti"
          value={overview.movement_count.toLocaleString("it-IT")}
        />
        <Metric
          label="Fondi personali"
          value={formatMoney(overview.funding_split.personal_funds)}
        />
        <Metric
          label="Benefit"
          value={formatMoney(overview.funding_split.benefit)}
        />
      </SimpleGrid>

      {overview.movement_count === 0 ? (
        <Paper className="empty-state" radius="lg">
          <Text fw={700}>Nessun movimento registrato in questo periodo.</Text>
          <Text c="dimmed">
            Bozze e movimenti da verificare restano fuori dai totali.
          </Text>
        </Paper>
      ) : (
        <Stack gap="xl">
          <SimpleGrid cols={{ base: 1, lg: 2 }} spacing="xl">
            <PurposeTable overview={overview} />
            <FundingTable overview={overview} />
          </SimpleGrid>
          <MerchantSection overview={overview} />
        </Stack>
      )}

      <ComparisonSection overview={overview} />
      <BudgetSection overview={overview} />
    </Stack>
  );
}

function Metric({ label, value }: { label: string; value: string }) {
  return (
    <Card className="analysis-metric" radius="lg">
      <Text className="metric__label">{label}</Text>
      <Text className="analysis-metric__value">{value}</Text>
    </Card>
  );
}

function PurposeTable({ overview }: { overview: RangeOverview }) {
  return (
    <Card className="table-card" radius="lg">
      <Title order={3}>Spesa per finalità</Title>
      <Table.ScrollContainer minWidth={460}>
        <Table striped highlightOnHover mt="md">
          <Table.Caption>
            Ripartizione esatta delle allocazioni per categoria, senza sommare
            genitori e figli.
          </Table.Caption>
          <Table.Thead>
            <Table.Tr>
              <Table.Th scope="col">Finalità</Table.Th>
              <Table.Th scope="col">Importo</Table.Th>
            </Table.Tr>
          </Table.Thead>
          <Table.Tbody>
            {overview.categories.map((category) => (
              <Table.Tr key={category.category_id}>
                <Table.Th scope="row">
                  {category.parent_name === null
                    ? category.category_name
                    : category.parent_name + " / " + category.category_name}
                </Table.Th>
                <Table.Td>{formatMoney(category.amount)}</Table.Td>
              </Table.Tr>
            ))}
          </Table.Tbody>
        </Table>
      </Table.ScrollContainer>
    </Card>
  );
}

function FundingTable({ overview }: { overview: RangeOverview }) {
  return (
    <Card className="table-card" radius="lg">
      <Title order={3}>Come hai pagato</Title>
      <Table.ScrollContainer minWidth={460}>
        <Table striped highlightOnHover mt="md">
          <Table.Caption>
            Consumo per fonte di pagamento; questi importi non sono saldi dei
            portafogli.
          </Table.Caption>
          <Table.Thead>
            <Table.Tr>
              <Table.Th scope="col">Fonte</Table.Th>
              <Table.Th scope="col">Classe</Table.Th>
              <Table.Th scope="col">Importo</Table.Th>
            </Table.Tr>
          </Table.Thead>
          <Table.Tbody>
            {overview.funding_sources.map((source) => (
              <Table.Tr key={source.name}>
                <Table.Th scope="row">{source.name}</Table.Th>
                <Table.Td>{fundingClassLabel(source.funding_class)}</Table.Td>
                <Table.Td>{formatMoney(source.amount)}</Table.Td>
              </Table.Tr>
            ))}
          </Table.Tbody>
        </Table>
      </Table.ScrollContainer>
    </Card>
  );
}

function MerchantSection({ overview }: { overview: RangeOverview }) {
  return (
    <section aria-labelledby="merchant-heading">
      <Title id="merchant-heading" order={2}>
        Spesa per esercente
      </Title>
      <Text c="dimmed" mt="xs">
        Ogni movimento contribuisce una sola volta con il proprio totale netto;
        i rimborsi mantengono il segno.
      </Text>
      <SimpleGrid cols={{ base: 1, lg: 2 }} mt="md" spacing="xl">
        <Card className="chart-card" radius="lg">
          <Suspense fallback={<Text>Caricamento del grafico esercenti…</Text>}>
            <MerchantChart merchants={overview.merchants} />
          </Suspense>
        </Card>
        <Card className="table-card" radius="lg">
          <Table.ScrollContainer minWidth={460}>
            <Table striped highlightOnHover>
              <Table.Caption>
                Alternativa tabellare esatta al grafico della spesa netta per
                esercente.
              </Table.Caption>
              <Table.Thead>
                <Table.Tr>
                  <Table.Th scope="col">Esercente</Table.Th>
                  <Table.Th scope="col">Movimenti</Table.Th>
                  <Table.Th scope="col">Importo netto</Table.Th>
                </Table.Tr>
              </Table.Thead>
              <Table.Tbody>
                {overview.merchants.map((entry) => (
                  <Table.Tr key={entry.merchant ?? "__missing_merchant__"}>
                    <Table.Th scope="row">
                      {entry.merchant ?? "Esercente non indicato"}
                    </Table.Th>
                    <Table.Td>{entry.movement_count}</Table.Td>
                    <Table.Td>{formatMoney(entry.amount)}</Table.Td>
                  </Table.Tr>
                ))}
              </Table.Tbody>
            </Table>
          </Table.ScrollContainer>
        </Card>
      </SimpleGrid>
    </section>
  );
}

function ComparisonSection({ overview }: { overview: RangeOverview }) {
  const comparison = overview.comparison;
  return (
    <section aria-labelledby="comparison-heading">
      <Title id="comparison-heading" order={2}>
        Confronto con il periodo precedente
      </Title>
      <Text c="dimmed" mt="xs">
        Il confronto usa soltanto i totali netti registrati. I budget restano
        separati e non sono prorazionati.
      </Text>
      <SimpleGrid cols={{ base: 1, lg: 2 }} mt="md" spacing="xl">
        <Card className="chart-card" radius="lg">
          <Suspense fallback={<Text>Caricamento del grafico confronto…</Text>}>
            <PeriodComparisonChart comparison={comparison} />
          </Suspense>
        </Card>
        <Card className="table-card" radius="lg">
          <Table.ScrollContainer minWidth={620}>
            <Table striped highlightOnHover>
              <Table.Caption>
                Alternativa tabellare esatta: valori assoluti, delta monetario
                e variazione percentuale.
              </Table.Caption>
              <Table.Thead>
                <Table.Tr>
                  <Table.Th scope="col">Periodo corrente</Table.Th>
                  <Table.Th scope="col">Periodo precedente</Table.Th>
                  <Table.Th scope="col">Delta</Table.Th>
                  <Table.Th scope="col">Variazione</Table.Th>
                </Table.Tr>
              </Table.Thead>
              <Table.Tbody>
                <Table.Tr>
                  <Table.Td>
                    <Text>{periodDatesFor(comparison.current_period)}</Text>
                    <Text fw={700}>{formatMoney(comparison.current_total)}</Text>
                  </Table.Td>
                  <Table.Td>
                    <Text>{periodDatesFor(comparison.previous_period)}</Text>
                    <Text fw={700}>{formatMoney(comparison.previous_total)}</Text>
                  </Table.Td>
                  <Table.Td>{formatMoney(comparison.delta)}</Table.Td>
                  <Table.Td>{formatPercentage(comparison)}</Table.Td>
                </Table.Tr>
              </Table.Tbody>
            </Table>
          </Table.ScrollContainer>
        </Card>
      </SimpleGrid>
    </section>
  );
}

function BudgetSection({ overview }: { overview: RangeOverview }) {
  if (overview.budgets === null) {
    return (
      <Alert color="blue" title="Budget non confrontati">
        I budget sono mensili e senza rollover: una posizione è disponibile
        solo quando il periodo copre un mese di calendario completo.
      </Alert>
    );
  }
  if (overview.budgets.positions.length === 0) {
    return (
      <Paper className="empty-state" radius="lg">
        <Text fw={700}>Non ci sono budget configurati per il confronto.</Text>
        <Text c="dimmed">
          L’analisi della spesa resta disponibile senza inventare un limite.
        </Text>
      </Paper>
    );
  }
  return (
    <section aria-labelledby="analysis-budget-heading">
      <Title id="analysis-budget-heading" order={2}>
        Posizione dei budget mensili
      </Title>
      <Card className="table-card" mt="md" radius="lg">
        <Table.ScrollContainer minWidth={620}>
          <Table striped highlightOnHover>
            <Table.Caption>
              Obiettivo, spesa registrata e residuo del mese completo.
            </Table.Caption>
            <Table.Thead>
              <Table.Tr>
                <Table.Th scope="col">Categoria</Table.Th>
                <Table.Th scope="col">Obiettivo</Table.Th>
                <Table.Th scope="col">Spesa</Table.Th>
                <Table.Th scope="col">Residuo</Table.Th>
              </Table.Tr>
            </Table.Thead>
            <Table.Tbody>
              {overview.budgets.positions.map((position) => (
                <Table.Tr key={position.category_name}>
                  <Table.Th scope="row">{position.category_name}</Table.Th>
                  <Table.Td>{formatMoney(position.target)}</Table.Td>
                  <Table.Td>{formatMoney(position.posted_spend)}</Table.Td>
                  <Table.Td>{formatMoney(position.remaining)}</Table.Td>
                </Table.Tr>
              ))}
            </Table.Tbody>
          </Table>
        </Table.ScrollContainer>
      </Card>
      {overview.budgets.unbudgeted_categories.length > 0 ? (
        <Alert
          color="orange"
          mt="md"
          title="Spesa senza un budget applicabile"
        >
          {overview.budgets.unbudgeted_categories.join(", ")}
        </Alert>
      ) : null}
    </section>
  );
}

function periodTitle(overview: RangeOverview): string {
  const period = overview.period;
  if (period.granularity === "week") {
    return "Settimana ISO " + period.iso_week + " del " + period.iso_year;
  }
  if (period.granularity === "month") {
    return monthLabel(period.start.slice(0, 7));
  }
  if (period.granularity === "year") {
    return period.start.slice(0, 4);
  }
  if (period.granularity === "day") {
    return formatDate(period.start);
  }
  return "Intervallo personalizzato";
}

function periodDates(overview: RangeOverview): string {
  return periodDatesFor(overview.period);
}

function periodDatesFor(period: ResolvedPeriod): string {
  if (period.start === period.end) {
    return formatDate(period.start);
  }
  return formatDate(period.start) + " – " + formatDate(period.end);
}

function formatPercentage(
  comparison: RangeOverview["comparison"],
): string {
  if (
    comparison.percentage_change_status === "previous_zero" ||
    comparison.percentage_change_basis_points === null
  ) {
    return "Non definita: il periodo precedente è pari a zero.";
  }
  const basisPoints = comparison.percentage_change_basis_points;
  const sign = basisPoints > 0 ? "+" : basisPoints < 0 ? "−" : "";
  const absolute = Math.abs(basisPoints);
  return (
    sign +
    Math.floor(absolute / 100).toLocaleString("it-IT") +
    "," +
    String(absolute % 100).padStart(2, "0") +
    "%"
  );
}

function formatDate(value: string): string {
  const [year, month, day] = value.split("-").map(Number);
  return new Intl.DateTimeFormat("it-IT", {
    dateStyle: "long",
    timeZone: "Europe/Rome",
  }).format(new Date(Date.UTC(year, month - 1, day)));
}

function fundingClassLabel(value: FundingClass): string {
  return {
    personal_funds: "Fondi personali",
    benefit: "Benefit",
    other: "Altro",
  }[value];
}
