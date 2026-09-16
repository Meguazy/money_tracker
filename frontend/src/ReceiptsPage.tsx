import {
  Alert,
  Badge,
  Button,
  Card,
  Group,
  Stack,
  Table,
  Text,
  Title,
} from "@mantine/core";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  type Receipt,
  type ReceiptStatus,
  approveReceipt,
  loadReceipts,
  openReceiptReview,
} from "./api";
import { AccessibleTableScrollContainer } from "./AccessibleTableScrollContainer";
import { formatOccurredAt } from "./format";

type ReceiptApis = {
  load: typeof loadReceipts;
  review: typeof openReceiptReview;
  approve: typeof approveReceipt;
};

type Decision = {
  action: "review" | "approve";
  receiptId: string;
  expectedVersion: number;
};

const defaultApis: ReceiptApis = {
  load: loadReceipts,
  review: openReceiptReview,
  approve: approveReceipt,
};

const statusLabels: Record<ReceiptStatus, string> = {
  stored: "Archiviato",
  needs_review: "Da approvare",
  approved: "Approvato",
  deleted: "Eliminato",
};

const statusColors: Record<ReceiptStatus, string> = {
  stored: "gray",
  needs_review: "yellow",
  approved: "green",
  deleted: "red",
};

const mediaLabels: Record<Receipt["media_type"], string> = {
  "image/jpeg": "JPEG",
  "image/png": "PNG",
  "image/webp": "WebP",
};

export function formatByteSize(bytes: number): string {
  if (bytes < 1024) {
    return `${bytes} B`;
  }
  const kibibytes = bytes / 1024;
  if (kibibytes < 1024) {
    return `${kibibytes.toFixed(1).replace(".", ",")} KiB`;
  }
  return `${(kibibytes / 1024).toFixed(1).replace(".", ",")} MiB`;
}

export function ReceiptsPage({ apis = defaultApis }: { apis?: ReceiptApis }) {
  const queryClient = useQueryClient();
  const register = useQuery({
    queryKey: ["receipts"],
    queryFn: ({ signal }) => apis.load(signal),
    retry: false,
  });
  const decision = useMutation({
    mutationFn: (choice: Decision) =>
      choice.action === "review"
        ? apis.review(choice.receiptId, choice.expectedVersion)
        : apis.approve(choice.receiptId, choice.expectedVersion),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["receipts"] });
    },
  });
  const receipts = register.data?.receipts ?? [];

  return (
    <main className="page" id="main-content">
      <Group align="end" className="page-heading" justify="space-between">
        <div>
          <Text className="eyebrow">Archivio privato</Text>
          <Title order={1}>Scontrini</Title>
          <Text c="dimmed" maw={650} mt="xs">
            Consulta i metadati degli originali conservati e completa la
            revisione obbligatoria. Le immagini restano fuori dal browser: qui
            non si aprono, non si scaricano e non si eliminano.
          </Text>
        </div>
      </Group>

      {register.isLoading ? (
        <Text role="status">Caricamento degli scontrini…</Text>
      ) : null}
      {register.isError ? (
        <Alert color="red" title="Scontrini non disponibili">
          <Stack align="flex-start" gap="sm">
            <Text>Non è stato possibile leggere l’archivio.</Text>
            <Button onClick={() => void register.refetch()} variant="light">
              Riprova
            </Button>
          </Stack>
        </Alert>
      ) : null}
      {decision.isError ? (
        <Alert color="red" mb="lg" title="Revisione non applicata">
          {decision.error instanceof Error
            ? decision.error.message
            : "Ricarica gli scontrini e riprova."}
        </Alert>
      ) : null}
      {register.isSuccess && receipts.length === 0 ? (
        <Card className="empty-state" padding="xl" radius="lg">
          <Text className="eyebrow">Archivio vuoto</Text>
          <Title order={2}>Nessuno scontrino conservato</Title>
          <Text c="dimmed" mt="xs">
            Gli originali importati compariranno qui con i loro metadati.
          </Text>
        </Card>
      ) : null}

      {receipts.length > 0 ? (
        <AccessibleTableScrollContainer
          ariaLabel="Archivio degli scontrini"
          minWidth={640}
        >
          <Table highlightOnHover striped>
            <Table.Caption>
              Originali conservati, dal più recente al più vecchio.
            </Table.Caption>
            <Table.Thead>
              <Table.Tr>
                <Table.Th scope="col">Nome</Table.Th>
                <Table.Th scope="col">Formato</Table.Th>
                <Table.Th scope="col">Dimensione</Table.Th>
                <Table.Th scope="col">Conservato il</Table.Th>
                <Table.Th scope="col">Stato</Table.Th>
                <Table.Th scope="col">Revisione</Table.Th>
              </Table.Tr>
            </Table.Thead>
            <Table.Tbody>
              {receipts.map((receipt) => (
                <ReceiptRow
                  disabled={decision.isPending}
                  key={receipt.id}
                  onApprove={() =>
                    decision.mutate({
                      action: "approve",
                      receiptId: receipt.id,
                      expectedVersion: receipt.version,
                    })
                  }
                  onReview={() =>
                    decision.mutate({
                      action: "review",
                      receiptId: receipt.id,
                      expectedVersion: receipt.version,
                    })
                  }
                  receipt={receipt}
                />
              ))}
            </Table.Tbody>
          </Table>
        </AccessibleTableScrollContainer>
      ) : null}
    </main>
  );
}

function ReceiptRow({
  receipt,
  disabled,
  onReview,
  onApprove,
}: {
  receipt: Receipt;
  disabled: boolean;
  onReview: () => void;
  onApprove: () => void;
}) {
  const name = receipt.original_filename ?? "Senza nome";

  return (
    <Table.Tr>
      <Table.Th scope="row">{name}</Table.Th>
      <Table.Td>{mediaLabels[receipt.media_type]}</Table.Td>
      <Table.Td>{formatByteSize(receipt.byte_size)}</Table.Td>
      <Table.Td>{formatOccurredAt(receipt.stored_at)}</Table.Td>
      <Table.Td>
        <Badge color={statusColors[receipt.status]} variant="light">
          {statusLabels[receipt.status]}
        </Badge>
      </Table.Td>
      <Table.Td>
        {receipt.status === "stored" ? (
          <Button disabled={disabled} onClick={onReview} variant="light">
            Apri la revisione di {name}
          </Button>
        ) : null}
        {receipt.status === "needs_review" ? (
          <Button disabled={disabled} onClick={onApprove}>
            Approva {name}
          </Button>
        ) : null}
        {receipt.status === "approved" ? (
          <Text c="dimmed" size="sm">
            Revisione completata.
          </Text>
        ) : null}
        {receipt.status === "deleted" ? (
          <Text c="dimmed" size="sm">
            Contenuto non più conservato.
          </Text>
        ) : null}
      </Table.Td>
    </Table.Tr>
  );
}
