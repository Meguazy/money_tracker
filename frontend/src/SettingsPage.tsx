import {
  Alert,
  Badge,
  Button,
  Card,
  Group,
  NativeSelect,
  Stack,
  Table,
  Text,
  Title,
} from "@mantine/core";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router-dom";

import {
  type DefaultFundingSource,
  loadProfileSettings,
  setDefaultFundingSource,
} from "./api";

export type SettingsApis = {
  load: typeof loadProfileSettings;
  setDefault: typeof setDefaultFundingSource;
};

const defaultApis: SettingsApis = {
  load: loadProfileSettings,
  setDefault: setDefaultFundingSource,
};

const fundingClassLabels = {
  personal_funds: "Fondi personali",
  benefit: "Benefit",
  other: "Altra risorsa",
} as const;

export function SettingsPage({
  apis = defaultApis,
}: {
  apis?: SettingsApis;
}) {
  const queryClient = useQueryClient();
  const [selectedSourceId, setSelectedSourceId] = useState("");
  const [confirmation, setConfirmation] =
    useState<DefaultFundingSource | null>(null);
  const [success, setSuccess] = useState(false);
  const settings = useQuery({
    queryKey: ["profile-settings"],
    queryFn: ({ signal }) => apis.load(signal),
    retry: false,
  });
  const changeDefault = useMutation({
    mutationFn: (source: DefaultFundingSource) =>
      apis.setDefault(source, settings.data?.default_funding_source ?? null),
    onSuccess: async () => {
      setConfirmation(null);
      setSelectedSourceId("");
      setSuccess(true);
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["profile-settings"] }),
        queryClient.invalidateQueries({
          queryKey: ["funding-source-management"],
        }),
        queryClient.invalidateQueries({ queryKey: ["transactions"] }),
        queryClient.invalidateQueries({ queryKey: ["monthly-overview"] }),
        queryClient.invalidateQueries({ queryKey: ["range-overview"] }),
      ]);
    },
  });

  const snapshot = settings.data;
  const currentDefault = snapshot?.default_funding_source ?? null;
  const effectiveSelection = selectedSourceId || currentDefault?.id || "";
  const selectedSource = snapshot?.available_default_sources.find(
    (source) => source.id === effectiveSelection,
  );
  const canChange =
    selectedSource !== undefined &&
    selectedSource.id !== currentDefault?.id &&
    !changeDefault.isPending;

  return (
    <main className="page" id="main-content">
      <Group align="end" className="page-heading" justify="space-between">
        <div>
          <Text className="eyebrow">Profilo locale</Text>
          <Title order={1}>Impostazioni</Title>
          <Text c="dimmed" maw={700} mt="xs">
            Consulta le preferenze V1 che guidano importi e calendario. Lingua,
            valuta e fuso orario restano fissi per mantenere coerenti movimenti,
            budget e analisi.
          </Text>
        </div>
      </Group>

      {settings.isLoading ? (
        <Text role="status">Caricamento delle impostazioni…</Text>
      ) : null}
      {settings.isError ? (
        <Alert color="red" title="Impostazioni non disponibili">
          <Stack align="flex-start" gap="sm">
            <Text>Non è stato possibile leggere la configurazione locale.</Text>
            <Button onClick={() => void settings.refetch()} variant="light">
              Riprova
            </Button>
          </Stack>
        </Alert>
      ) : null}

      {snapshot ? (
        <Stack gap="xl">
          <Card className="table-card" padding="lg" radius="lg">
            <Title mb="sm" order={2}>
              Preferenze V1
            </Title>
            <Table>
              <Table.Caption>
                Lingua, valuta e calendario sono sola lettura nella versione 1.
              </Table.Caption>
              <Table.Tbody>
                <FixedSetting
                  label="Lingua dell’applicazione"
                  value={`Italiano (${snapshot.locale})`}
                />
                <FixedSetting
                  label="Valuta operativa"
                  value={`Euro (${snapshot.currency})`}
                />
                <FixedSetting
                  label="Fuso orario"
                  value={snapshot.timezone}
                />
              </Table.Tbody>
            </Table>
          </Card>

          <Card className="editor-card" radius="lg">
            <Stack gap="md">
              <div>
                <Text className="eyebrow">Preferenza modificabile</Text>
                <Title order={2}>Fonte di pagamento predefinita</Title>
                <Text c="dimmed" mt="xs">
                  Viene applicata quando un nuovo messaggio di spesa non indica
                  come è stato pagato. La conferma del movimento mostra sempre
                  la fonte scelta.
                </Text>
              </div>

              {snapshot.available_default_sources.length === 0 ? (
                <Card className="empty-state" padding="lg" radius="md">
                  <Stack align="flex-start" gap="sm">
                    <Title order={3}>Nessuna fonte disponibile</Title>
                    <Text>
                      Crea una fonte attiva prima di scegliere quella
                      predefinita.
                    </Text>
                    <Button component={Link} to="/fonti" variant="light">
                      Vai alle fonti
                    </Button>
                  </Stack>
                </Card>
              ) : (
                <>
                  <NativeSelect
                    data={[
                      { label: "Seleziona una fonte", value: "" },
                      ...snapshot.available_default_sources.map((source) => ({
                        label: `${source.name} · ${fundingClassLabels[source.funding_class]}`,
                        value: source.id,
                      })),
                    ]}
                    disabled={changeDefault.isPending}
                    label="Fonte predefinita"
                    onChange={(event) => {
                      setSelectedSourceId(event.currentTarget.value);
                      setConfirmation(null);
                      setSuccess(false);
                    }}
                    value={effectiveSelection}
                  />
                  <Group>
                    <Button
                      disabled={!canChange}
                      loading={changeDefault.isPending}
                      onClick={() => {
                        if (selectedSource) {
                          setConfirmation(selectedSource);
                        }
                      }}
                    >
                      Salva fonte predefinita
                    </Button>
                    <Button component={Link} to="/fonti" variant="subtle">
                      Gestisci fonti e wallet
                    </Button>
                  </Group>
                </>
              )}

              {confirmation ? (
                <Alert color="yellow" title="Conferma il cambio">
                  <Stack align="flex-start" gap="sm">
                    <Text>
                      Le nuove spese senza metodo indicato useranno
                      {` ${confirmation.name}`}. I movimenti già registrati non
                      cambieranno.
                    </Text>
                    <Group>
                      <Button
                        loading={changeDefault.isPending}
                        onClick={() => changeDefault.mutate(confirmation)}
                      >
                        Conferma modifica
                      </Button>
                      <Button
                        disabled={changeDefault.isPending}
                        onClick={() => setConfirmation(null)}
                        variant="default"
                      >
                        Annulla
                      </Button>
                    </Group>
                  </Stack>
                </Alert>
              ) : null}
              {changeDefault.isError ? (
                <Alert color="red" title="Modifica non applicata">
                  {changeDefault.error instanceof Error
                    ? changeDefault.error.message
                    : "Non è stato possibile aggiornare la fonte predefinita."}
                </Alert>
              ) : null}
              {success ? (
                <Alert color="green" role="status" title="Impostazione aggiornata">
                  La fonte predefinita è stata aggiornata.
                </Alert>
              ) : null}
            </Stack>
          </Card>
        </Stack>
      ) : null}
    </main>
  );
}

function FixedSetting({ label, value }: { label: string; value: string }) {
  return (
    <Table.Tr>
      <Table.Th scope="row">{label}</Table.Th>
      <Table.Td>{value}</Table.Td>
      <Table.Td>
        <Badge color="gray" variant="light">
          Fissa in V1
        </Badge>
      </Table.Td>
    </Table.Tr>
  );
}
