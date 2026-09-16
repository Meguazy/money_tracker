import "@mantine/core/styles.css";
import "./styles.css";

import { MantineProvider, createTheme } from "@mantine/core";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter } from "react-router-dom";

import { App } from "./App";

const theme = createTheme({
  primaryColor: "dark",
  colors: {
    gray: [
      "#f8f9fa",
      "#f1f3f5",
      "#e9ecef",
      "#dee2e6",
      "#ced4da",
      "#adb5bd",
      "#5f6872",
      "#495057",
      "#343a40",
      "#212529",
    ],
    red: [
      "#fff5f5",
      "#ffe3e3",
      "#ffc9c9",
      "#ffa8a8",
      "#ff8787",
      "#ff6b6b",
      "#b42318",
      "#9a1d14",
      "#801910",
      "#67140d",
    ],
  },
  defaultRadius: "md",
  fontFamily:
    'Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif',
  headings: {
    fontFamily: 'Georgia, "Times New Roman", serif',
    fontWeight: "600",
  },
});

const queryClient = new QueryClient({
  defaultOptions: {
    queries: { refetchOnWindowFocus: false, staleTime: 30_000 },
  },
});

const root = document.getElementById("root");
if (root === null) {
  throw new Error("Dashboard root is missing");
}

createRoot(root).render(
  <StrictMode>
    <MantineProvider theme={theme}>
      <QueryClientProvider client={queryClient}>
        <BrowserRouter>
          <App />
        </BrowserRouter>
      </QueryClientProvider>
    </MantineProvider>
  </StrictMode>,
);
