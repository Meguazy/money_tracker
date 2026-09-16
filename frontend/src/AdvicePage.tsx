import {
  Alert,
  Badge,
  Button,
  Card,
  Group,
  Stack,
  Table,
  Text,
  Textarea,
  Title,
} from "@mantine/core";
import { useMutation } from "@tanstack/react-query";
import { type FormEvent, useState } from "react";
import { Link } from "react-router-dom";

import {
  type AdviceEvidence,
  type AdviceResponse,
  type FundingClass,
  type Money,
  requestAdvice,
} from "./api";
import { AccessibleTableScrollContainer } from "./AccessibleTableScrollContainer";
import { formatMoney, monthLabel } from "./format";

export type AdviceRequester = (message: string) => Promise<AdviceResponse>;

const fundingClassLabels: Record<FundingClass, string> = {
  personal_funds: "Fondi personali",
  benefit: "Benefit",
  other: "Altra risorsa",
};

export function AdvicePage({ requester = requestAdvice }: { requester?: AdviceRequester }) {
  const [message, setMessage] = useState("");
  const [lastRequest, setLastRequest] = useState("");
  const advice = useMutation({ mutationFn: requester });

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const normalized = message.trim();
    if (!normalized || advice.isPending) {
      return;
    }
    setLastRequest(normalized);
    advice.mutate(normalized);
  }

  return (
    <main className="page" id="main-content">
      <Group align="end" className="page-heading" justify="space-between">
        <div>
          <Text className="eyebrow">Consultazione guidata</Text>
          <Title order={1}>Consigli verificabili sui tuoi dati</Title>
          <Text c="dimmed" maw={720} mt="xs">
            Chiedi di budget, spesa per categoria o uso delle fonti. La risposta
            mostra soltanto numeri calcolati dai movimenti registrati.
          </Text>
        </div>
        <Badge color="teal" variant="light">
          Sola lettura
        </Badge>
      </Group>

      <Stack gap="xl">
        <Card className="editor-card" radius="lg">
          <form onSubmit={submit}>
            <Stack align="flex-start" gap="md">
              <Textarea
                autosize
                description="Per esempio: Come vanno i miei budget questo mese?"
                disabled={advice.isPending}
                label="La tua domanda"
                maxLength={1000}
                minRows={3}
                onChange={(event) => setMessage(event.currentTarget.value)}
                required
                value={message}
                w="100%"
              />
              <Button disabled={!message.trim()} loading={advice.isPending} type="submit">
                Consulta i dati
              </Button>
            </Stack>
          </form>
        </Card>

        {!advice.data && !advice.isError && !advice.isPending ? (
          <Card className="empty-state" padding="lg" radius="lg">
            <Title order={2}>Nessuna consultazione ancora</Title>
            <Text c="dimmed" mt="xs">
              Il modello sceglie una capacità promossa; i valori arrivano poi da
              calcoli deterministici, non dal testo generato.
            </Text>
          </Card>
        ) : null}

        {advice.isPending ? (
          <Text aria-live="polite" role="status">
            Consultazione dei dati in corso…
          </Text>
        ) : null}

        {advice.isError ? (
          <Alert color="red" role="alert" title="Consigli non disponibili">
            <Stack align="flex-start" gap="sm">
              <Text>{advice.error.message}</Text>
              <Button
                disabled={!lastRequest || advice.isPending}
                onClick={() => advice.mutate(lastRequest)}
                variant="light"
              >
                Riprova
              </Button>
            </Stack>
          </Alert>
        ) : null}

        {advice.data?.status === "needs_clarification" ? (
          <Clarification reason={advice.data.reason} />
        ) : null}
        {advice.data?.status === "answered" ? (
          <Evidence evidence={advice.data.evidence} />
        ) : null}

        <Alert color="blue" title="Confronti nel tempo">
          Il contratto advice promosso non confronta due periodi. Per periodo
          corrente e precedente, delta e variazioni percentuali usa la pagina{" "}
          <Text component={Link} fw={700} to="/analisi">
            Analisi
          </Text>
          .
        </Alert>
      </Stack>
    </main>
  );
}

function Clarification({
  reason,
}: {
  reason: Extract<AdviceResponse, { status: "needs_clarification" }>["reason"];
}) {
  const descriptions = {
    category_not_found:
      "La categoria richiesta non è attiva o non è riconoscibile. Indica una categoria disponibile.",
    capability_not_available:
      "La richiesta richiede una capacità che non appartiene all’advice promosso.",
    request_not_understood:
      "Specifica se vuoi consultare budget, spesa per categoria o uso delle fonti.",
  } as const;
  return (
    <Alert color="yellow" role="status" title="Serve una precisazione">
      {descriptions[reason]}
    </Alert>
  );
}

function Evidence({ evidence }: { evidence: AdviceEvidence }) {
  if (evidence.kind === "category_spending") {
    return (
      <Stack gap="md">
        <EvidenceHeading period={evidence.period} title={`Spesa per ${evidence.category_name}`} />
        <Card className="metric metric--coral" radius="lg">
          <Text className="metric__label">Spesa netta registrata</Text>
          <Text className="metric__value">{formatMoney(evidence.posted_spend)}</Text>
        </Card>
        <FundingSplitTable split={evidence.funding_split} />
        <Alert color="yellow" title="Nessuna soglia per il giudizio">
          Questa evidenza non contiene un budget né un confronto storico: mostra
          la spesa esatta, ma non conclude se sia troppo alta.
        </Alert>
        <EvidenceProvenance />
      </Stack>
    );
  }
  if (evidence.kind === "funding_class_split") {
    return (
      <Stack gap="md">
        <EvidenceHeading period={evidence.period} title="Uso delle fonti" />
        <FundingSplitTable split={evidence.funding_split} />
        {evidence.funding_sources.length > 0 ? (
          <Card className="table-card" radius="lg">
            <AccessibleTableScrollContainer
              ariaLabel="Consumo per fonte"
              minWidth={480}
            >
              <Table striped>
                <Table.Caption>Consumo netto registrato per fonte.</Table.Caption>
                <Table.Thead>
                  <Table.Tr>
                    <Table.Th scope="col">Fonte</Table.Th>
                    <Table.Th scope="col">Classe</Table.Th>
                    <Table.Th scope="col">Consumo</Table.Th>
                  </Table.Tr>
                </Table.Thead>
                <Table.Tbody>
                  {evidence.funding_sources.map((source) => (
                    <Table.Tr key={`${source.funding_class}:${source.name}`}>
                      <Table.Th scope="row">{source.name}</Table.Th>
                      <Table.Td>{fundingClassLabels[source.funding_class]}</Table.Td>
                      <Table.Td>{formatMoney(source.amount)}</Table.Td>
                    </Table.Tr>
                  ))}
                </Table.Tbody>
              </Table>
            </AccessibleTableScrollContainer>
          </Card>
        ) : (
          <Alert color="yellow" title="Nessuna fonte con movimenti registrati">
            Il periodo non contiene consumi attribuiti a fonti configurate.
          </Alert>
        )}
        <Alert color="blue" title="Consumo, non saldo disponibile">
          Questi importi descrivono i pagamenti registrati per fonte; non sono il
          saldo contabile o disponibile di un wallet.
        </Alert>
        <EvidenceProvenance />
      </Stack>
    );
  }
  return <BudgetEvidence evidence={evidence} />;
}

function BudgetEvidence({
  evidence,
}: {
  evidence: Extract<AdviceEvidence, { kind: "budgets_at_risk" }>;
}) {
  return (
    <Stack gap="md">
      <EvidenceHeading period={evidence.period} title="Evidenze sui budget" />
      {evidence.assessment_status === "insufficient_data" ? (
        <Alert color="yellow" role="status" title="Dati insufficienti">
          Non risultano budget configurati né spese registrate senza budget nel
          mese richiesto. Non viene formulato alcun giudizio.
        </Alert>
      ) : null}
      {evidence.positions.length > 0 ? (
        <Card className="table-card" radius="lg">
          <AccessibleTableScrollContainer
            ariaLabel="Evidenze sui budget"
            minWidth={560}
          >
            <Table striped>
              <Table.Caption>
                Obiettivi e posizione calcolati sui soli movimenti registrati.
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
                {evidence.positions.map((position) => (
                  <Table.Tr key={position.category_name}>
                    <Table.Th scope="row">{position.category_name}</Table.Th>
                    <Table.Td>{formatMoney(position.target)}</Table.Td>
                    <Table.Td>{formatMoney(position.posted_spend)}</Table.Td>
                    <Table.Td>{formatMoney(position.remaining)}</Table.Td>
                  </Table.Tr>
                ))}
              </Table.Tbody>
            </Table>
          </AccessibleTableScrollContainer>
        </Card>
      ) : null}
      {evidence.unbudgeted_categories.length > 0 ? (
        <Alert color="orange" title="Spesa senza budget applicabile">
          {evidence.unbudgeted_categories.join(", ")}
        </Alert>
      ) : null}
      <EvidenceProvenance />
    </Stack>
  );
}

function EvidenceHeading({
  period,
  title,
}: {
  period: { year: number; month: number };
  title: string;
}) {
  const token = `${period.year}-${String(period.month).padStart(2, "0")}`;
  return (
    <div>
      <Text className="eyebrow">{monthLabel(token)}</Text>
      <Title order={2}>{title}</Title>
    </div>
  );
}

function FundingSplitTable({
  split,
}: {
  split: Record<FundingClass, Money>;
}) {
  return (
    <Card className="table-card" radius="lg">
      <Table>
        <Table.Caption>
          Ripartizione esatta e alternativa tabellare dell’evidenza numerica.
        </Table.Caption>
        <Table.Thead>
          <Table.Tr>
            <Table.Th scope="col">Classe della fonte</Table.Th>
            <Table.Th scope="col">Importo</Table.Th>
          </Table.Tr>
        </Table.Thead>
        <Table.Tbody>
          {(Object.keys(fundingClassLabels) as FundingClass[]).map((kind) => (
            <Table.Tr key={kind}>
              <Table.Th scope="row">{fundingClassLabels[kind]}</Table.Th>
              <Table.Td>{formatMoney(split[kind])}</Table.Td>
            </Table.Tr>
          ))}
        </Table.Tbody>
      </Table>
    </Card>
  );
}

function EvidenceProvenance() {
  return (
    <Text c="dimmed" size="sm">
      Evidenza derivata dal tool deterministico promosso, in EUR e centesimi
      interi, sui soli movimenti registrati nel calendario Europe/Rome.
    </Text>
  );
}
