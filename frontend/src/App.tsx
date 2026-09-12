import { AppShell, Group, Text } from "@mantine/core";
import { NavLink, Navigate, Route, Routes } from "react-router-dom";

import { OverviewPage } from "./OverviewPage";
import { TransactionsPage } from "./TransactionsPage";

export function App() {
  return (
    <AppShell header={{ height: 72 }} padding={0}>
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
              <Text className="brand-note">locale · privato · verificabile</Text>
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
              to="/movimenti"
            >
              Movimenti
            </NavLink>
          </nav>
          <Text className="local-state">127.0.0.1</Text>
        </Group>
      </AppShell.Header>
      <AppShell.Main>
        <Routes>
          <Route element={<OverviewPage />} path="/" />
          <Route element={<TransactionsPage />} path="/movimenti" />
          <Route element={<Navigate replace to="/" />} path="*" />
        </Routes>
      </AppShell.Main>
    </AppShell>
  );
}
