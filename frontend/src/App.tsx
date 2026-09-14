import { AppShell, Group, Text } from "@mantine/core";
import { NavLink, Navigate, Route, Routes } from "react-router-dom";
import { lazy, Suspense } from "react";

const AnalyticsPage = lazy(() =>
  import("./AnalyticsPage").then((module) => ({
    default: module.AnalyticsPage,
  })),
);
const BudgetsPage = lazy(() =>
  import("./BudgetsPage").then((module) => ({ default: module.BudgetsPage })),
);
const CategoriesPage = lazy(() =>
  import("./CategoriesPage").then((module) => ({
    default: module.CategoriesPage,
  })),
);
const FundingSourcesPage = lazy(() =>
  import("./FundingSourcesPage").then((module) => ({
    default: module.FundingSourcesPage,
  })),
);
const MessagingPage = lazy(() =>
  import("./MessagingPage").then((module) => ({
    default: module.MessagingPage,
  })),
);
const OverviewPage = lazy(() =>
  import("./OverviewPage").then((module) => ({ default: module.OverviewPage })),
);
const ReceiptsPage = lazy(() =>
  import("./ReceiptsPage").then((module) => ({ default: module.ReceiptsPage })),
);
const TransactionsPage = lazy(() =>
  import("./TransactionsPage").then((module) => ({
    default: module.TransactionsPage,
  })),
);

export function App() {
  return (
    <AppShell header={{ height: { base: 184, sm: 72 } }} padding={0}>
      <a className="skip-link" href="#main-content">
        Vai al contenuto
      </a>
      <AppShell.Header className="app-header">
        <Group h="100%" justify="space-between" px={{ base: "md", sm: "xl" }}>
          <Group gap="sm">
            <span aria-hidden="true" className="brand-mark">
              M
            </span>
            <div>
              <Text className="brand-name">Money Tracker</Text>
              <Text className="brand-note">
                locale · privato · verificabile
              </Text>
            </div>
          </Group>
          <nav aria-label="Navigazione principale" className="main-nav">
            <NavLink
              className={({ isActive }) => (isActive ? "active" : "")}
              end
              to="/"
            >
              Quadro
            </NavLink>
            <NavLink
              className={({ isActive }) => (isActive ? "active" : "")}
              to="/analisi"
            >
              Analisi
            </NavLink>
            <NavLink
              className={({ isActive }) => (isActive ? "active" : "")}
              to="/movimenti"
            >
              Movimenti
            </NavLink>
            <NavLink
              className={({ isActive }) => (isActive ? "active" : "")}
              to="/categorie"
            >
              Categorie
            </NavLink>
            <NavLink
              className={({ isActive }) => (isActive ? "active" : "")}
              to="/fonti"
            >
              Fonti
            </NavLink>
            <NavLink
              className={({ isActive }) => (isActive ? "active" : "")}
              to="/budget"
            >
              Budget
            </NavLink>
            <NavLink
              className={({ isActive }) => (isActive ? "active" : "")}
              to="/scontrini"
            >
              Scontrini
            </NavLink>
            <NavLink
              className={({ isActive }) => (isActive ? "active" : "")}
              to="/messaggistica"
            >
              Messaggistica
            </NavLink>
          </nav>
          <Text className="local-state">127.0.0.1</Text>
        </Group>
      </AppShell.Header>
      <AppShell.Main>
        <Suspense
          fallback={
            <main className="page" id="main-content">
              <Text aria-live="polite">Caricamento della pagina…</Text>
            </main>
          }
        >
          <Routes>
            <Route element={<OverviewPage />} path="/" />
            <Route element={<AnalyticsPage />} path="/analisi" />
            <Route element={<TransactionsPage />} path="/movimenti" />
            <Route element={<CategoriesPage />} path="/categorie" />
            <Route element={<FundingSourcesPage />} path="/fonti" />
            <Route element={<BudgetsPage />} path="/budget" />
            <Route element={<ReceiptsPage />} path="/scontrini" />
            <Route element={<MessagingPage />} path="/messaggistica" />
            <Route element={<Navigate replace to="/" />} path="*" />
          </Routes>
        </Suspense>
      </AppShell.Main>
    </AppShell>
  );
}
