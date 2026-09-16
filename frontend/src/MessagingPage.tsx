import {
  Alert,
  Badge,
  Button,
  Card,
  Group,
  SimpleGrid,
  Stack,
  Table,
  Text,
  Title,
} from "@mantine/core";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  type InboxDeadLetter,
  type MessagingOperations,
  type MessagingQueueState,
  type OutboxDeadLetter,
  loadMessagingOperations,
  replayInboxItem,
  resendOutboxItem,
} from "./api";
import { AccessibleTableScrollContainer } from "./AccessibleTableScrollContainer";
import { formatOccurredAt } from "./format";

type MessagingApis = {
  load: typeof loadMessagingOperations;
  replay: typeof replayInboxItem;
  resend: typeof resendOutboxItem;
};

type Requeue = {
  queue: "inbox" | "outbox";
  itemId: string;
  observedReplayCount: number;
};

const defaultApis: MessagingApis = {
  load: loadMessagingOperations,
  replay: replayInboxItem,
  resend: resendOutboxItem,
};

const environmentLabels: Record<MessagingOperations["environment"], string> = {
  production: "Produzione",
  test: "Test",
};

/**
 * Names the bounded reason code a failed attempt recorded.
 *
 * An unknown code is shown as it arrived rather than hidden: the code is a
 * machine-readable identifier by contract, never owner text, and an operator
 * who can read it can search for it.
 */
const errorLabels: Record<string, string> = {
  database_unavailable: "Database non raggiungibile",
  dispatch_failed: "Elaborazione non riuscita",
  model_unavailable: "Modello non raggiungibile",
  telegram_unavailable: "Telegram non raggiungibile",
  telegram_rejected: "Telegram ha rifiutato l’invio",
  validation_failed: "Messaggio non elaborabile",
};

export function describeError(code: string | null): string {
  if (code === null) {
    return "Nessun codice registrato";
  }
  return errorLabels[code] ?? code;
}

export function MessagingPage({
  apis = defaultApis,
}: {
  apis?: MessagingApis;
}) {
  const queryClient = useQueryClient();
  const operations = useQuery({
    queryKey: ["messaging"],
    queryFn: ({ signal }) => apis.load(signal),
    retry: false,
  });
  const requeue = useMutation({
    mutationFn: (choice: Requeue) =>
      choice.queue === "inbox"
        ? apis.replay(choice.itemId, choice.observedReplayCount)
        : apis.resend(choice.itemId, choice.observedReplayCount),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["messaging"] });
    },
  });
  const state = operations.data;
  const inboxFailures = state?.inbox_dead_letters ?? [];
  const outboxFailures = state?.outbox_dead_letters ?? [];

  return (
    <main className="page" id="main-content">
      <Group align="end" className="page-heading" justify="space-between">
        <div>
          <Text className="eyebrow">Canale Telegram</Text>
          <Title order={1}>Messaggistica</Title>
          <Text c="dimmed" maw={650} mt="xs">
            Controlla le code durevoli del bot configurato e rimetti in coda un
            lavoro o una risposta che ha esaurito i tentativi. Qui non compare
            nessun messaggio: solo conteggi, tempi e codici di errore.
          </Text>
        </div>
        {state ? (
          <Badge size="lg" variant="light">
            Ambiente {environmentLabels[state.environment]}
          </Badge>
        ) : null}
      </Group>

      {operations.isLoading ? (
        <Text role="status">Caricamento dello stato della messaggistica…</Text>
      ) : null}
      {operations.isError ? (
        <Alert color="red" title="Stato non disponibile">
          <Stack align="flex-start" gap="sm">
            <Text>Non è stato possibile leggere le code del bot.</Text>
            <Button onClick={() => void operations.refetch()} variant="light">
              Riprova
            </Button>
          </Stack>
        </Alert>
      ) : null}
      {requeue.isError ? (
        <Alert color="red" mb="lg" title="Rimessa in coda non applicata">
          {requeue.error instanceof Error
            ? requeue.error.message
            : "Ricarica lo stato della messaggistica e riprova."}
        </Alert>
      ) : null}

      {state ? (
        <>
          {state.owner_paired ? null : (
            <Alert color="yellow" mb="lg" title="Nessun proprietario associato">
              Il bot non ha ancora un abbinamento. Crea un codice con
              <Text component="span" fw={600}>
                {" money-tracker-telegram pair "}
              </Text>
              e invialo nella chat privata.
            </Alert>
          )}
          {state.downtime_exceeds_update_retention ? (
            <Alert color="orange" mb="lg" title="Interruzione prolungata">
              L’ultimo poll è più vecchio delle 24 ore in cui Telegram conserva
              gli aggiornamenti non consegnati. I messaggi di quel periodo non
              sono recuperabili e vanno reinviati.
            </Alert>
          ) : null}

          <SimpleGrid cols={{ base: 1, sm: 2 }} mb="lg" spacing="lg">
            <QueueCard
              oldestLabel="In attesa dal"
              queue={state.inbox}
              title="Lavori in ingresso"
            />
            <QueueCard
              oldestLabel="In coda dal"
              queue={state.outbox}
              title="Risposte in uscita"
            />
          </SimpleGrid>

          <Card mb="lg" padding="lg" radius="lg" withBorder>
            <SimpleGrid cols={{ base: 1, sm: 3 }} spacing="md">
              <Fact label="Prossimo offset" value={String(state.next_offset)} />
              <Fact
                label="Ultimo poll"
                value={
                  state.last_poll_at === null
                    ? "Mai"
                    : formatOccurredAt(state.last_poll_at)
                }
              />
              <Fact
                label="Interazioni attive"
                value={String(state.active_actions)}
              />
            </SimpleGrid>
          </Card>

          <Title mb="sm" order={2}>
            Lavori falliti
          </Title>
          {inboxFailures.length === 0 ? (
            <Text c="dimmed" mb="lg">
              Nessun lavoro in ingresso ha esaurito i tentativi.
            </Text>
          ) : (
            <AccessibleTableScrollContainer
              ariaLabel="Dead letter in ingresso"
              minWidth={640}
            >
              <Table highlightOnHover mb="lg" striped>
                <Table.Caption>
                  Lavori in ingresso che hanno esaurito i cinque tentativi
                  automatici, in ordine di arrivo.
                </Table.Caption>
                <Table.Thead>
                  <Table.Tr>
                    <Table.Th scope="col">Aggiornamento</Table.Th>
                    <Table.Th scope="col">Accettato il</Table.Th>
                    <Table.Th scope="col">Tentativi</Table.Th>
                    <Table.Th scope="col">Rimesse in coda</Table.Th>
                    <Table.Th scope="col">Errore</Table.Th>
                    <Table.Th scope="col">Azione</Table.Th>
                  </Table.Tr>
                </Table.Thead>
                <Table.Tbody>
                  {inboxFailures.map((item) => (
                    <InboxRow
                      disabled={requeue.isPending}
                      item={item}
                      key={item.inbox_id}
                      onReplay={() =>
                        requeue.mutate({
                          queue: "inbox",
                          itemId: item.inbox_id,
                          observedReplayCount: item.replay_count,
                        })
                      }
                    />
                  ))}
                </Table.Tbody>
              </Table>
            </AccessibleTableScrollContainer>
          )}

          <Title mb="sm" order={2}>
            Risposte non consegnate
          </Title>
          {outboxFailures.length === 0 ? (
            <Text c="dimmed">
              Nessuna risposta in uscita ha esaurito i tentativi.
            </Text>
          ) : (
            <>
              <Text c="dimmed" maw={650} mb="sm">
                Telegram non offre una chiave di idempotenza per l’invio: se un
                tentativo precedente era ambiguo, un nuovo invio può far vedere
                al proprietario lo stesso messaggio due volte. Nessuna scrittura
                finanziaria viene ripetuta.
              </Text>
              <AccessibleTableScrollContainer
                ariaLabel="Dead letter in uscita"
                minWidth={640}
              >
                <Table highlightOnHover striped>
                  <Table.Caption>
                    Risposte logiche che hanno esaurito i cinque tentativi di
                    consegna, dalla più vecchia.
                  </Table.Caption>
                  <Table.Thead>
                    <Table.Tr>
                      <Table.Th scope="col">Tipo</Table.Th>
                      <Table.Th scope="col">Creata il</Table.Th>
                      <Table.Th scope="col">Tentativi</Table.Th>
                      <Table.Th scope="col">Reinvii</Table.Th>
                      <Table.Th scope="col">Errore</Table.Th>
                      <Table.Th scope="col">Azione</Table.Th>
                    </Table.Tr>
                  </Table.Thead>
                  <Table.Tbody>
                    {outboxFailures.map((item) => (
                      <OutboxRow
                        disabled={requeue.isPending}
                        item={item}
                        key={item.outbox_id}
                        onResend={() =>
                          requeue.mutate({
                            queue: "outbox",
                            itemId: item.outbox_id,
                            observedReplayCount: item.replay_count,
                          })
                        }
                      />
                    ))}
                  </Table.Tbody>
                </Table>
              </AccessibleTableScrollContainer>
            </>
          )}
        </>
      ) : null}
    </main>
  );
}

function Fact({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <Text className="eyebrow">{label}</Text>
      <Text fw={600}>{value}</Text>
    </div>
  );
}

function QueueCard({
  title,
  queue,
  oldestLabel,
}: {
  title: string;
  queue: MessagingQueueState;
  oldestLabel: string;
}) {
  return (
    <Card padding="lg" radius="lg" withBorder>
      <Title order={2} size="h4">
        {title}
      </Title>
      <SimpleGrid cols={3} mt="md" spacing="sm">
        <Fact label="In attesa" value={String(queue.pending)} />
        <Fact label="In ritentativo" value={String(queue.retrying)} />
        <Fact label="Falliti" value={String(queue.dead_letters)} />
      </SimpleGrid>
      <Text c="dimmed" mt="md" size="sm">
        {oldestLabel}:{" "}
        {queue.oldest_at === null ? "—" : formatOccurredAt(queue.oldest_at)}
      </Text>
    </Card>
  );
}

function InboxRow({
  item,
  disabled,
  onReplay,
}: {
  item: InboxDeadLetter;
  disabled: boolean;
  onReplay: () => void;
}) {
  return (
    <Table.Tr>
      <Table.Th scope="row">#{item.update_id}</Table.Th>
      <Table.Td>{formatOccurredAt(item.accepted_at)}</Table.Td>
      <Table.Td>{item.attempt_count}</Table.Td>
      <Table.Td>{item.replay_count}</Table.Td>
      <Table.Td>{describeError(item.last_error_code)}</Table.Td>
      <Table.Td>
        <Button disabled={disabled} onClick={onReplay} variant="light">
          Rimetti in coda l’aggiornamento {item.update_id}
        </Button>
      </Table.Td>
    </Table.Tr>
  );
}

function OutboxRow({
  item,
  disabled,
  onResend,
}: {
  item: OutboxDeadLetter;
  disabled: boolean;
  onResend: () => void;
}) {
  return (
    <Table.Tr>
      <Table.Th scope="row">{item.response_kind}</Table.Th>
      <Table.Td>{formatOccurredAt(item.created_at)}</Table.Td>
      <Table.Td>{item.attempt_count}</Table.Td>
      <Table.Td>{item.replay_count}</Table.Td>
      <Table.Td>{describeError(item.last_error_code)}</Table.Td>
      <Table.Td>
        <Button disabled={disabled} onClick={onResend} variant="light">
          Rinvia la risposta {item.response_kind}
        </Button>
      </Table.Td>
    </Table.Tr>
  );
}
