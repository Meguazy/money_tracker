import {
  Alert,
  Badge,
  Button,
  Card,
  Group,
  SimpleGrid,
  Stack,
  Text,
  TextInput,
  Title,
} from "@mantine/core";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import {
  type ManagedCategory,
  archiveCategory,
  loadCategoryManagement,
  renameCategory,
} from "./api";

export type CategoryManagementApis = {
  load: typeof loadCategoryManagement;
  rename: typeof renameCategory;
  archive: typeof archiveCategory;
};

const defaultApis: CategoryManagementApis = {
  load: loadCategoryManagement,
  rename: renameCategory,
  archive: archiveCategory,
};

type Change =
  | { action: "rename"; category: ManagedCategory; name: string }
  | { action: "archive"; category: ManagedCategory };

export function CategoryManagementPanel({
  apis = defaultApis,
}: {
  apis?: CategoryManagementApis;
}) {
  const queryClient = useQueryClient();
  const [editingId, setEditingId] = useState<string | null>(null);
  const [name, setName] = useState("");
  const [confirmingId, setConfirmingId] = useState<string | null>(null);
  const management = useQuery({
    queryKey: ["category-management"],
    queryFn: ({ signal }) => apis.load(signal),
    retry: false,
  });
  const change = useMutation({
    mutationFn: (request: Change) =>
      request.action === "rename"
        ? apis.rename(request.category.id, request.name, request.category.version)
        : apis.archive(request.category.id, request.category.version),
    onSuccess: async () => {
      setEditingId(null);
      setConfirmingId(null);
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["category-management"] }),
        queryClient.invalidateQueries({ queryKey: ["category-proposals"] }),
        queryClient.invalidateQueries({ queryKey: ["transactions"] }),
        queryClient.invalidateQueries({ queryKey: ["monthly-overview"] }),
        queryClient.invalidateQueries({ queryKey: ["budget-plan"] }),
        queryClient.invalidateQueries({ queryKey: ["range-overview"] }),
      ]);
    },
  });

  const activeChildren = new Set(
    management.data?.categories
      .filter((category) => category.status === "active")
      .flatMap((category) =>
        category.parent_id === null ? [] : [category.parent_id],
      ) ?? [],
  );

  return (
    <section aria-labelledby="category-management-title">
      <Group align="end" justify="space-between" mb="lg">
        <div>
          <Text className="eyebrow">Categorie in uso</Text>
          <Title id="category-management-title" order={2}>
            Gestisci nomi e disponibilità
          </Title>
          <Text c="dimmed" maw={700} mt="xs">
            La rinomina conserva movimenti e budget. L’archiviazione impedisce
            nuove assegnazioni senza cancellare lo storico.
          </Text>
        </div>
      </Group>

      {management.isLoading ? (
        <Text role="status">Caricamento delle categorie…</Text>
      ) : null}
      {management.isError ? (
        <Alert color="red" title="Categorie non disponibili">
          <Stack align="flex-start" gap="sm">
            <Text>Non è stato possibile leggere la tassonomia.</Text>
            <Button onClick={() => void management.refetch()} variant="light">
              Riprova
            </Button>
          </Stack>
        </Alert>
      ) : null}
      {change.isError ? (
        <Alert color="red" mb="lg" title="Modifica non applicata">
          {change.error instanceof Error
            ? change.error.message
            : "Ricarica le categorie e riprova."}
        </Alert>
      ) : null}
      {management.data?.warnings.map((warning) => (
        <Alert color="yellow" key={warning.code} mb="md" title="Tassonomia ampia">
          {warning.message}
        </Alert>
      ))}

      <SimpleGrid cols={{ base: 1, md: 2 }}>
        {management.data?.categories.map((category) => {
          const hasActiveChildren = activeChildren.has(category.id);
          const isEditing = editingId === category.id;
          const isConfirming = confirmingId === category.id;
          return (
            <Card className="editor-card" key={category.id} padding="lg" radius="lg">
              <Stack gap="sm">
                <Group justify="space-between">
                  <div>
                    <Text fw={700}>{category.name}</Text>
                    <Text c="dimmed" size="sm">
                      {category.parent_name === null
                        ? "Categoria principale"
                        : `Sotto-categoria di ${category.parent_name}`}
                    </Text>
                  </div>
                  <Group gap="xs">
                    {category.seeded ? <Badge variant="light">Iniziale</Badge> : null}
                    <Badge
                      color={category.status === "active" ? "teal" : "gray"}
                      variant="light"
                    >
                      {category.status === "active" ? "Attiva" : "Archiviata"}
                    </Badge>
                  </Group>
                </Group>
                <Text size="sm">{category.description}</Text>
                {category.aliases.length > 0 ? (
                  <Text c="dimmed" size="sm">
                    Alias: {category.aliases.join(", ")}
                  </Text>
                ) : null}

                {category.status === "active" && isEditing ? (
                  <form
                    onSubmit={(event) => {
                      event.preventDefault();
                      change.mutate({ action: "rename", category, name });
                    }}
                  >
                    <Group align="end">
                      <TextInput
                        disabled={change.isPending}
                        label={`Nuovo nome per ${category.name}`}
                        maxLength={200}
                        onChange={(event) => setName(event.currentTarget.value)}
                        required
                        value={name}
                      />
                      <Button disabled={change.isPending} type="submit">
                        Salva nome
                      </Button>
                      <Button
                        onClick={() => setEditingId(null)}
                        type="button"
                        variant="subtle"
                      >
                        Annulla
                      </Button>
                    </Group>
                  </form>
                ) : null}

                {category.status === "active" && !isEditing ? (
                  <Group align="center">
                    <Button
                      disabled={change.isPending}
                      onClick={() => {
                        setEditingId(category.id);
                        setConfirmingId(null);
                        setName(category.name);
                      }}
                      variant="light"
                    >
                      Rinomina
                    </Button>
                    {isConfirming ? (
                      <>
                        <Button
                          color="red"
                          disabled={change.isPending}
                          onClick={() => change.mutate({ action: "archive", category })}
                        >
                          Conferma archiviazione
                        </Button>
                        <Button onClick={() => setConfirmingId(null)} variant="subtle">
                          Annulla
                        </Button>
                      </>
                    ) : (
                      <Button
                        color="red"
                        disabled={change.isPending || hasActiveChildren}
                        onClick={() => {
                          setConfirmingId(category.id);
                          setEditingId(null);
                        }}
                        variant="subtle"
                      >
                        Archivia
                      </Button>
                    )}
                    {hasActiveChildren ? (
                      <Text c="dimmed" size="xs">
                        Archivia prima le sotto-categorie attive.
                      </Text>
                    ) : null}
                  </Group>
                ) : null}
              </Stack>
            </Card>
          );
        })}
      </SimpleGrid>
    </section>
  );
}
