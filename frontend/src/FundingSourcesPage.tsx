import {
  Alert,
  Badge,
  Button,
  Card,
  Fieldset,
  Group,
  NativeSelect,
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
  type FundingClass,
  type FundingSourceInput,
  type ManagedFundingSource,
  type WalletEntryInput,
  addWalletEntry,
  archiveFundingSource,
  createFundingSource,
  loadFundingSources,
  renameFundingSource,
  setDefaultFundingSource,
} from "./api";
import { AccessibleTableScrollContainer } from "./AccessibleTableScrollContainer";
import {
  formatMoney,
  formatOccurredAt,
  parseEuroToMinorUnits,
  romeDateTimeInput,
  romeDateTimeInputToInstant,
} from "./format";

export type FundingSourceApis = {
  load: typeof loadFundingSources;
  create: typeof createFundingSource;
  rename: typeof renameFundingSource;
  archive: typeof archiveFundingSource;
  setDefault: typeof setDefaultFundingSource;
  addEntry: typeof addWalletEntry;
};

const defaultApis: FundingSourceApis = {
  load: loadFundingSources,
  create: createFundingSource,
  rename: renameFundingSource,
  archive: archiveFundingSource,
  setDefault: setDefaultFundingSource,
  addEntry: addWalletEntry,
};

type SourceChange =
  | { action: "create"; input: FundingSourceInput }
  | { action: "rename"; source: ManagedFundingSource; name: string }
  | { action: "archive"; source: ManagedFundingSource }
  | {
      action: "set-default";
      source: ManagedFundingSource;
      previousDefault: ManagedFundingSource | null;
    };

type Confirmation = { action: "archive" | "set-default"; sourceId: string };

type WalletEditor = {
  sourceId: string;
  entryType: WalletEntryInput["entry_type"];
  amount: string;
  occurredAt: string;
  expiresOn: string;
};

const fundingClassLabels: Record<FundingClass, string> = {
  personal_funds: "Fondi personali",
  benefit: "Benefit",
  other: "Altra risorsa",
};

const entryLabels = {
  opening: "Saldo iniziale",
  credit: "Ricarica",
  debit: "Spesa",
  refund: "Rimborso",
  expiry: "Scadenza",
  adjustment: "Rettifica",
} as const;

function emptyWalletEditor(): WalletEditor {
  return {
    sourceId: "",
    entryType: "credit",
    amount: "",
    occurredAt: romeDateTimeInput(),
    expiresOn: "",
  };
}

export function FundingSourcesPage({
  apis = defaultApis,
}: {
  apis?: FundingSourceApis;
}) {
  const queryClient = useQueryClient();
  const [newName, setNewName] = useState("");
  const [newClass, setNewClass] = useState<FundingClass>("personal_funds");
  const [newMode, setNewMode] = useState<FundingSourceInput["balance_mode"]>(
    "untracked",
  );
  const [editingId, setEditingId] = useState<string | null>(null);
  const [renameValue, setRenameValue] = useState("");
  const [confirmation, setConfirmation] = useState<Confirmation | null>(null);
  const [walletEditor, setWalletEditor] = useState<WalletEditor>(emptyWalletEditor);
  const [formError, setFormError] = useState<string | null>(null);
  const management = useQuery({
    queryKey: ["funding-source-management"],
    queryFn: ({ signal }) => apis.load(signal),
    retry: false,
  });
  const invalidateDependentQueries = async () => {
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: ["funding-source-management"] }),
      queryClient.invalidateQueries({ queryKey: ["transactions"] }),
      queryClient.invalidateQueries({ queryKey: ["monthly-overview"] }),
      queryClient.invalidateQueries({ queryKey: ["range-overview"] }),
    ]);
  };
  const sourceChange = useMutation({
    mutationFn: (change: SourceChange) => {
      if (change.action === "create") {
        return apis.create(change.input);
      }
      if (change.action === "rename") {
        return apis.rename(change.source.id, change.name, change.source.version);
      }
      if (change.action === "archive") {
        return apis.archive(change.source.id, change.source.version);
      }
      return apis.setDefault(change.source, change.previousDefault);
    },
    onSuccess: async () => {
      setNewName("");
      setEditingId(null);
      setConfirmation(null);
      setFormError(null);
      await invalidateDependentQueries();
    },
  });
  const walletChange = useMutation({
    mutationFn: ({ sourceId, input }: { sourceId: string; input: WalletEntryInput }) =>
      apis.addEntry(sourceId, input),
    onSuccess: async () => {
      setWalletEditor(emptyWalletEditor());
      setFormError(null);
      await invalidateDependentQueries();
    },
  });

  const previousDefault =
    management.data?.sources.find((source) => source.is_default) ?? null;
  const pending = sourceChange.isPending || walletChange.isPending;
  const operationError = sourceChange.error ?? walletChange.error;

  function submitSource(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (newName.trim().length === 0) {
      setFormError("Inserisci un nome per la fonte.");
      return;
    }
    setFormError(null);
    sourceChange.mutate({
      action: "create",
      input: {
        name: newName.trim(),
        funding_class: newClass,
        balance_mode: newMode,
        is_default: false,
      },
    });
  }

  function submitWallet(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const minorUnits = parseEuroToMinorUnits(walletEditor.amount);
    if (minorUnits === null) {
      setFormError("Inserisci un importo non nullo con al massimo due decimali.");
      return;
    }
    if (walletEditor.entryType !== "adjustment" && minorUnits < 0) {
      setFormError("Saldo iniziale e ricarica richiedono un importo positivo.");
      return;
    }
    const occurredAt = romeDateTimeInputToInstant(walletEditor.occurredAt);
    if (occurredAt === null) {
      setFormError("Inserisci una data e un'ora valide.");
      return;
    }
    if (
      walletEditor.entryType === "adjustment" &&
      minorUnits < 0 &&
      !window.confirm(
        "Confermi la rettifica negativa? Il movimento resterà nello storico del wallet.",
      )
    ) {
      return;
    }
    setFormError(null);
    walletChange.mutate({
      sourceId: walletEditor.sourceId,
      input: {
        entry_type: walletEditor.entryType,
        amount: { minor_units: minorUnits, currency: "EUR" },
        occurred_at: occurredAt.toISOString(),
        expires_on:
          walletEditor.entryType === "adjustment" || walletEditor.expiresOn === ""
            ? null
            : walletEditor.expiresOn,
      },
    });
  }

  return (
    <main className="page" id="main-content">
      <Group align="end" className="page-heading" justify="space-between">
        <div>
          <Text className="eyebrow">Fonti di pagamento</Text>
          <Title order={1}>Da dove arrivano i fondi</Title>
          <Text c="dimmed" maw={720} mt="xs">
            Le fonti descrivono il mezzo di pagamento, non lo scopo della spesa.
            Solo i wallet tracciati espongono un saldo autorevole.
          </Text>
        </div>
      </Group>

      <Card className="editor-card" mb="xl" radius="lg">
        <form onSubmit={submitSource}>
          <Stack gap="md">
            <div>
              <Text className="eyebrow">Nuova fonte</Text>
              <Title order={2}>Aggiungi un mezzo di pagamento</Title>
            </div>
            <SimpleGrid cols={{ base: 1, sm: 3 }}>
              <TextInput
                disabled={pending}
                label="Nome"
                maxLength={200}
                onChange={(event) => setNewName(event.currentTarget.value)}
                required
                value={newName}
              />
              <NativeSelect
                data={[
                  { label: "Fondi personali", value: "personal_funds" },
                  { label: "Benefit", value: "benefit" },
                  { label: "Altra risorsa", value: "other" },
                ]}
                disabled={pending}
                label="Classe economica"
                onChange={(event) =>
                  setNewClass(event.currentTarget.value as FundingClass)
                }
                value={newClass}
              />
              <NativeSelect
                data={[
                  { label: "Saldo non tracciato", value: "untracked" },
                  { label: "Wallet con ledger", value: "ledger" },
                ]}
                disabled={pending}
                label="Gestione saldo"
                onChange={(event) =>
                  setNewMode(
                    event.currentTarget.value as FundingSourceInput["balance_mode"],
                  )
                }
                value={newMode}
              />
            </SimpleGrid>
            <Button disabled={pending} type="submit">
              Aggiungi fonte
            </Button>
          </Stack>
        </form>
      </Card>

      {management.isLoading ? <Text role="status">Caricamento delle fonti…</Text> : null}
      {management.isError ? (
        <Alert color="red" title="Fonti non disponibili">
          <Stack align="flex-start" gap="sm">
            <Text>Non è stato possibile leggere fonti e wallet.</Text>
            <Button onClick={() => void management.refetch()} variant="light">
              Riprova
            </Button>
          </Stack>
        </Alert>
      ) : null}
      {formError !== null ? (
        <Alert color="red" mb="lg" title="Controlla i dati">
          {formError}
        </Alert>
      ) : null}
      {operationError !== null ? (
        <Alert color="red" mb="lg" title="Modifica non applicata">
          {operationError instanceof Error
            ? operationError.message
            : "Ricarica le fonti e riprova."}
        </Alert>
      ) : null}
      {management.data?.sources.length === 0 ? (
        <Card className="empty-state" radius="lg">
          <Title order={2}>Nessuna fonte configurata</Title>
          <Text c="dimmed" mt="xs">
            Aggiungi una fonte per usarla nei movimenti.
          </Text>
        </Card>
      ) : null}

      <Stack gap="xl">
        {management.data?.sources.map((source) => (
          <SourceCard
            confirmation={confirmation}
            disabled={pending}
            editingId={editingId}
            key={source.id}
            onArchive={() => sourceChange.mutate({ action: "archive", source })}
            onBeginRename={() => {
              setEditingId(source.id);
              setRenameValue(source.name);
              setConfirmation(null);
            }}
            onCancelRename={() => setEditingId(null)}
            onCancelConfirmation={() => setConfirmation(null)}
            onConfirm={(action) => setConfirmation({ action, sourceId: source.id })}
            onRename={() =>
              sourceChange.mutate({ action: "rename", source, name: renameValue })
            }
            onSetDefault={() =>
              sourceChange.mutate({
                action: "set-default",
                source,
                previousDefault,
              })
            }
            onShowWallet={() =>
              setWalletEditor({
                ...emptyWalletEditor(),
                sourceId: source.id,
                entryType: source.entries.length === 0 ? "opening" : "credit",
              })
            }
            renameValue={renameValue}
            setRenameValue={setRenameValue}
            source={source}
            walletEditor={walletEditor}
          />
        ))}
      </Stack>

      {walletEditor.sourceId !== "" ? (
        <WalletEntryEditor
          disabled={pending}
          editor={walletEditor}
          onCancel={() => setWalletEditor(emptyWalletEditor())}
          onChange={setWalletEditor}
          onSubmit={submitWallet}
          source={management.data?.sources.find(
            (source) => source.id === walletEditor.sourceId,
          )}
        />
      ) : null}
    </main>
  );
}

function SourceCard({
  source,
  disabled,
  editingId,
  renameValue,
  confirmation,
  walletEditor,
  setRenameValue,
  onBeginRename,
  onCancelRename,
  onCancelConfirmation,
  onRename,
  onConfirm,
  onArchive,
  onSetDefault,
  onShowWallet,
}: {
  source: ManagedFundingSource;
  disabled: boolean;
  editingId: string | null;
  renameValue: string;
  confirmation: Confirmation | null;
  walletEditor: WalletEditor;
  setRenameValue: (name: string) => void;
  onBeginRename: () => void;
  onCancelRename: () => void;
  onCancelConfirmation: () => void;
  onRename: () => void;
  onConfirm: (action: Confirmation["action"]) => void;
  onArchive: () => void;
  onSetDefault: () => void;
  onShowWallet: () => void;
}) {
  const isEditing = editingId === source.id;
  const confirmationAction =
    confirmation?.sourceId === source.id ? confirmation.action : null;
  const balanceIsZero =
    source.availability === null ||
    (source.availability.booked_balance.minor_units === 0 &&
      source.availability.available_balance.minor_units === 0);
  const canArchive = !source.is_default && balanceIsZero;

  return (
    <Card className="editor-card" padding="xl" radius="lg">
      <Stack gap="lg">
        <Group align="flex-start" justify="space-between">
          <div>
            <Text className="eyebrow">{fundingClassLabels[source.funding_class]}</Text>
            <Title order={2}>{source.name}</Title>
            <Text c="dimmed" size="sm">
              {source.balance_mode === "ledger"
                ? "Wallet con saldo tracciato"
                : "Fonte senza saldo tracciato"}
            </Text>
          </div>
          <Group gap="xs">
            {source.is_default ? (
              <Badge color="yellow" variant="light">
                Predefinita
              </Badge>
            ) : null}
            <Badge
              color={source.status === "active" ? "teal" : "gray"}
              variant="light"
            >
              {source.status === "active" ? "Attiva" : "Archiviata"}
            </Badge>
          </Group>
        </Group>

        {source.availability !== null ? (
          <SimpleGrid cols={{ base: 1, sm: 2 }}>
            <Card bg="dark.8" c="white" padding="lg" radius="md">
              <Text size="sm">Disponibile ora</Text>
              <Text fw={700} size="xl">
                {formatMoney(source.availability.available_balance)}
              </Text>
            </Card>
            <Card bg="gray.0" padding="lg" radius="md">
              <Text size="sm">Saldo contabile</Text>
              <Text fw={700} size="xl">
                {formatMoney(source.availability.booked_balance)}
              </Text>
            </Card>
          </SimpleGrid>
        ) : null}

        {source.status === "active" && isEditing ? (
          <form
            onSubmit={(event) => {
              event.preventDefault();
              onRename();
            }}
          >
            <Group align="end">
              <TextInput
                disabled={disabled}
                label={`Nuovo nome per ${source.name}`}
                maxLength={200}
                onChange={(event) => setRenameValue(event.currentTarget.value)}
                required
                value={renameValue}
              />
              <Button disabled={disabled} type="submit">Salva nome</Button>
              <Button onClick={onCancelRename} type="button" variant="subtle">Annulla</Button>
            </Group>
          </form>
        ) : null}

        {source.status === "active" && !isEditing ? (
          <Group align="center">
            <Button disabled={disabled} onClick={onBeginRename} variant="light">
              Rinomina
            </Button>
            {!source.is_default ? (
              confirmationAction === "set-default" ? (
                <>
                  <Button disabled={disabled} onClick={onSetDefault}>
                    Conferma come predefinita
                  </Button>
                  <Button onClick={onCancelConfirmation} variant="subtle">
                    Annulla
                  </Button>
                </>
              ) : (
                <Button
                  disabled={disabled}
                  onClick={() => onConfirm("set-default")}
                  variant="outline"
                >
                  Imposta predefinita
                </Button>
              )
            ) : null}
            {source.balance_mode === "ledger" ? (
              <Button
                disabled={disabled || walletEditor.sourceId === source.id}
                onClick={onShowWallet}
                variant="outline"
              >
                Registra movimento wallet
              </Button>
            ) : null}
            {confirmationAction === "archive" ? (
              <>
                <Button color="red" disabled={disabled} onClick={onArchive}>
                  Conferma archiviazione
                </Button>
                <Button onClick={onCancelConfirmation} variant="subtle">
                  Annulla
                </Button>
              </>
            ) : (
              <Button
                color="red"
                disabled={disabled || !canArchive}
                onClick={() => onConfirm("archive")}
                variant="subtle"
              >
                Archivia
              </Button>
            )}
          </Group>
        ) : null}
        {source.status === "active" && !canArchive ? (
          <Text c="dimmed" size="xs">
            {source.is_default
              ? "Scegli prima un’altra fonte predefinita."
              : "Azzera saldo contabile e disponibilità prima di archiviare."}
          </Text>
        ) : null}

        {source.availability?.expiring.length ? (
          <div>
            <Text fw={700}>Credito con scadenza</Text>
            {source.availability.expiring.map((lot) => (
              <Text key={lot.expires_on} size="sm">
                {formatMoney(lot.amount)} · utilizzabile fino al {lot.expires_on}
              </Text>
            ))}
          </div>
        ) : null}

        {source.balance_mode === "ledger" ? (
          <WalletHistory source={source} />
        ) : null}
      </Stack>
    </Card>
  );
}

function WalletHistory({ source }: { source: ManagedFundingSource }) {
  if (source.entries.length === 0) {
    return <Text c="dimmed">Nessun movimento wallet registrato.</Text>;
  }
  return (
    <AccessibleTableScrollContainer
      ariaLabel="Cronologia del wallet"
      minWidth={620}
    >
      <Table striped withTableBorder>
        <Table.Thead>
          <Table.Tr>
            <Table.Th>Movimento</Table.Th>
            <Table.Th>Data</Table.Th>
            <Table.Th>Importo</Table.Th>
            <Table.Th>Scadenza</Table.Th>
          </Table.Tr>
        </Table.Thead>
        <Table.Tbody>
          {source.entries.map((entry) => (
            <Table.Tr key={entry.id}>
              <Table.Td>{entryLabels[entry.entry_type]}</Table.Td>
              <Table.Td>{formatOccurredAt(entry.occurred_at)}</Table.Td>
              <Table.Td>{formatMoney(entry.amount)}</Table.Td>
              <Table.Td>{entry.expires_on ?? "—"}</Table.Td>
            </Table.Tr>
          ))}
        </Table.Tbody>
      </Table>
    </AccessibleTableScrollContainer>
  );
}

function WalletEntryEditor({
  source,
  editor,
  disabled,
  onChange,
  onCancel,
  onSubmit,
}: {
  source: ManagedFundingSource | undefined;
  editor: WalletEditor;
  disabled: boolean;
  onChange: (editor: WalletEditor) => void;
  onCancel: () => void;
  onSubmit: (event: FormEvent<HTMLFormElement>) => void;
}) {
  if (source === undefined) {
    return null;
  }
  return (
    <Card className="editor-card" id="wallet-entry-editor" mt="xl" radius="lg">
      <form onSubmit={onSubmit}>
        <Stack gap="md">
          <Group justify="space-between">
            <div>
              <Text className="eyebrow">Movimento immutabile</Text>
              <Title order={2}>Aggiorna {source.name}</Title>
            </div>
            <Button onClick={onCancel} type="button" variant="subtle">Chiudi</Button>
          </Group>
          <Fieldset legend="Dati del wallet">
            <SimpleGrid cols={{ base: 1, sm: 2 }}>
              <NativeSelect
                data={[
                  ...(source.entries.length === 0
                    ? [{ label: "Saldo iniziale", value: "opening" }]
                    : []),
                  { label: "Ricarica", value: "credit" },
                  { label: "Rettifica", value: "adjustment" },
                ]}
                disabled={disabled}
                label="Tipo"
                onChange={(event) =>
                  onChange({
                    ...editor,
                    entryType: event.currentTarget.value as WalletEntryInput["entry_type"],
                    expiresOn:
                      event.currentTarget.value === "adjustment" ? "" : editor.expiresOn,
                  })
                }
                value={editor.entryType}
              />
              <TextInput
                disabled={disabled}
                label="Importo in euro"
                onChange={(event) => onChange({ ...editor, amount: event.currentTarget.value })}
                placeholder="25,00 oppure -5,00 per una rettifica"
                required
                value={editor.amount}
              />
              <TextInput
                disabled={disabled}
                label="Data e ora"
                onChange={(event) => onChange({ ...editor, occurredAt: event.currentTarget.value })}
                required
                type="datetime-local"
                value={editor.occurredAt}
              />
              <TextInput
                disabled={disabled || editor.entryType === "adjustment"}
                label="Scadenza, facoltativa"
                min={editor.occurredAt.slice(0, 10)}
                onChange={(event) => onChange({ ...editor, expiresOn: event.currentTarget.value })}
                type="date"
                value={editor.expiresOn}
              />
            </SimpleGrid>
          </Fieldset>
          <Text c="dimmed" size="sm">
            Le rettifiche negative non possono portare il saldo contabile sotto
            zero; il credito scaduto resta indisponibile. Una registrazione non
            viene cancellata: eventuali correzioni producono un nuovo movimento.
          </Text>
          <Button disabled={disabled} type="submit">Registra movimento</Button>
        </Stack>
      </form>
    </Card>
  );
}
