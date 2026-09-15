import {
  Alert,
  Badge,
  Button,
  Card,
  Group,
  NativeSelect,
  SimpleGrid,
  Stack,
  Text,
  Title,
} from "@mantine/core";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import {
  type CategoryProposal,
  type CategoryReviewQueue,
  approveCategoryProposal,
  cancelCategoryProposal,
  loadCategoryProposals,
  reuseCategoryProposal,
} from "./api";
import {
  CategoryManagementPanel,
  type CategoryManagementApis,
} from "./CategoryManagementPanel";
import { formatMoney } from "./format";

type CategoryReviewApis = {
  load: typeof loadCategoryProposals;
  approve: typeof approveCategoryProposal;
  reuse: typeof reuseCategoryProposal;
  cancel: typeof cancelCategoryProposal;
};

type Decision =
  | { action: "approve"; proposalId: string }
  | { action: "cancel"; proposalId: string }
  | { action: "reuse"; proposalId: string; categoryName: string };

const defaultApis: CategoryReviewApis = {
  load: loadCategoryProposals,
  approve: approveCategoryProposal,
  reuse: reuseCategoryProposal,
  cancel: cancelCategoryProposal,
};

function occurredAt(value: string): string {
  return new Intl.DateTimeFormat("it-IT", {
    dateStyle: "medium",
    timeStyle: "short",
    timeZone: "Europe/Rome",
  }).format(new Date(value));
}

export function CategoriesPage({
  apis = defaultApis,
  managementApis,
}: {
  apis?: CategoryReviewApis;
  managementApis?: CategoryManagementApis;
}) {
  const queryClient = useQueryClient();
  const [selectedCategories, setSelectedCategories] = useState<
    Record<string, string>
  >({});
  const queue = useQuery({
    queryKey: ["category-proposals"],
    queryFn: ({ signal }) => apis.load(signal),
    retry: false,
  });
  const decision = useMutation({
    mutationFn: (choice: Decision) => {
      if (choice.action === "approve") {
        return apis.approve(choice.proposalId);
      }
      if (choice.action === "cancel") {
        return apis.cancel(choice.proposalId);
      }
      return apis.reuse(
        choice.proposalId,
        choice.categoryName,
      );
    },
    onSuccess: async () => {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["category-proposals"] }),
        queryClient.invalidateQueries({ queryKey: ["transactions"] }),
        queryClient.invalidateQueries({ queryKey: ["monthly-overview"] }),
        queryClient.invalidateQueries({ queryKey: ["budget-plan"] }),
        queryClient.invalidateQueries({ queryKey: ["range-overview"] }),
      ]);
    },
  });

  return (
    <main className="page" id="main-content">
      <Group align="end" className="page-heading" justify="space-between">
        <div>
          <Text className="eyebrow">Tassonomia controllata</Text>
          <Title order={1}>Categorie</Title>
          <Text c="dimmed" maw={650} mt="xs">
            Mantieni ordinata la tassonomia e decidi le proposte che tengono un
            movimento in attesa.
          </Text>
        </div>
      </Group>

      <CategoryManagementPanel apis={managementApis} />

      <Title id="category-proposals-title" mb="lg" mt="xl" order={2}>
        Proposte in attesa
      </Title>
      {queue.isLoading ? (
        <Text role="status">Caricamento delle proposte…</Text>
      ) : null}
      {queue.isError ? (
        <Alert color="red" title="Proposte non disponibili">
          <Stack align="flex-start" gap="sm">
            <Text>Non è stato possibile leggere le revisioni in attesa.</Text>
            <Button onClick={() => void queue.refetch()} variant="light">
              Riprova
            </Button>
          </Stack>
        </Alert>
      ) : null}
      {decision.isError ? (
        <Alert color="red" mb="lg" title="Decisione non applicata">
          {decision.error instanceof Error
            ? decision.error.message
            : "Ricarica le proposte e riprova."}
        </Alert>
      ) : null}
      {queue.data?.proposals.length === 0 ? (
        <Card className="empty-state" padding="xl" radius="lg">
          <Text className="eyebrow">Tutto risolto</Text>
          <Title order={2}>Nessuna proposta in attesa</Title>
          <Text c="dimmed" mt="xs">
            I movimenti che richiedono una categoria compariranno qui.
          </Text>
        </Card>
      ) : null}

      <Stack gap="xl">
        {queue.data?.proposals.map((proposal) => (
          <ProposalCard
            activeCategories={queue.data.active_categories}
            disabled={decision.isPending}
            key={proposal.id}
            onApprove={() =>
              decision.mutate({ action: "approve", proposalId: proposal.id })
            }
            onCancel={() =>
              decision.mutate({ action: "cancel", proposalId: proposal.id })
            }
            onReuse={(categoryName) =>
              decision.mutate({
                action: "reuse",
                proposalId: proposal.id,
                categoryName,
              })
            }
            proposal={proposal}
            selectedCategory={selectedCategories[proposal.id] ?? ""}
            setSelectedCategory={(categoryName) =>
              setSelectedCategories((current) => ({
                ...current,
                [proposal.id]: categoryName,
              }))
            }
          />
        ))}
      </Stack>
    </main>
  );
}

function ProposalCard({
  proposal,
  activeCategories,
  selectedCategory,
  disabled,
  setSelectedCategory,
  onApprove,
  onReuse,
  onCancel,
}: {
  proposal: CategoryProposal;
  activeCategories: CategoryReviewQueue["active_categories"];
  selectedCategory: string;
  disabled: boolean;
  setSelectedCategory: (categoryName: string) => void;
  onApprove: () => void;
  onReuse: (categoryName: string) => void;
  onCancel: () => void;
}) {
  const selectId = `existing-category-${proposal.id}`;

  return (
    <Card className="editor-card" padding="xl" radius="lg">
      <Stack gap="lg">
        <Group justify="space-between">
          <div>
            <Text className="eyebrow">Categoria proposta</Text>
            <Title order={2}>{proposal.proposed_name}</Title>
          </div>
          <Badge color="yellow" variant="light">
            In attesa
          </Badge>
        </Group>

        <SimpleGrid cols={{ base: 1, md: 2 }}>
          <div>
            <Text fw={700}>Ambito</Text>
            <Text>{proposal.description}</Text>
            <Text c="dimmed" mt="xs" size="sm">
              {proposal.parent_name === null
                ? "Categoria principale"
                : `Sotto-categoria di ${proposal.parent_name}`}
            </Text>
          </div>
          <div>
            <Text fw={700}>Perché è stata proposta</Text>
            <Text>{proposal.rationale}</Text>
          </div>
        </SimpleGrid>

        <Card bg="gray.0" padding="md" radius="md">
          <Text fw={700}>Movimento in attesa</Text>
          <Text>{proposal.expense.description}</Text>
          <Text c="dimmed" size="sm">
            {formatMoney(proposal.expense.amount)} ·{" "}
            {occurredAt(proposal.expense.occurred_at)}
            {proposal.expense.merchant === null
              ? ""
              : ` · ${proposal.expense.merchant}`}
          </Text>
        </Card>

        <Stack gap="sm">
          <Text fw={700}>Decisione</Text>
          <Group>
            <Button disabled={disabled} onClick={onApprove}>
              Approva e registra
            </Button>
            {proposal.alternative_categories.map((category) => (
              <Button
                disabled={disabled}
                key={category.id}
                onClick={() => onReuse(category.name)}
                variant="light"
              >
                Usa {category.name}
              </Button>
            ))}
          </Group>
          <Group align="end">
            <NativeSelect
              data={[
                { label: "Scegli una categoria attiva", value: "" },
                ...activeCategories.map((category) => ({
                  label: category.name,
                  value: category.name,
                })),
              ]}
              disabled={disabled}
              id={selectId}
              label="Usa un’altra categoria esistente"
              onChange={(event) => setSelectedCategory(event.currentTarget.value)}
              value={selectedCategory}
            />
            <Button
              disabled={disabled || selectedCategory.length === 0}
              onClick={() => onReuse(selectedCategory)}
              variant="outline"
            >
              Usa la categoria scelta
            </Button>
          </Group>
          <Text c="dimmed" size="sm">
            {proposal.can_repropose
              ? "Per generare un’altra proposta continua la conversazione Telegram; qui puoi sempre scegliere manualmente una categoria attiva."
              : "Il ciclo di proposte è esaurito: scegli una categoria attiva oppure annulla."}
          </Text>
          <Button
            color="red"
            disabled={disabled}
            onClick={onCancel}
            variant="subtle"
          >
            Annulla proposta e bozza
          </Button>
        </Stack>
      </Stack>
    </Card>
  );
}
