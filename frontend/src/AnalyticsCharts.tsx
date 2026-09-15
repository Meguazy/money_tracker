import { useEffect, useRef } from "react";
import { BarChart } from "echarts/charts";
import {
  AriaComponent,
  GridComponent,
  TooltipComponent,
} from "echarts/components";
import * as echarts from "echarts/core";
import { SVGRenderer } from "echarts/renderers";

import type { RangeOverview } from "./api";

echarts.use([
  BarChart,
  AriaComponent,
  GridComponent,
  TooltipComponent,
  SVGRenderer,
]);

const euroFormatter = (value: number) =>
  new Intl.NumberFormat("it-IT", {
    style: "currency",
    currency: "EUR",
  }).format(value / 100);

export function MerchantChart({
  merchants,
}: Pick<RangeOverview, "merchants">) {
  const chartNode = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (chartNode.current === null) {
      return;
    }
    const chart = echarts.init(chartNode.current, undefined, {
      renderer: "svg",
    });
    chart.setOption({
      animation: false,
      aria: {
        enabled: true,
        decal: { show: true },
        description:
          "Totali netti dei movimenti registrati raggruppati per esercente.",
      },
      color: ["#d85f45"],
      grid: { left: 150, right: 24, top: 16, bottom: 32 },
      tooltip: { trigger: "axis", valueFormatter: euroFormatter },
      xAxis: {
        type: "value",
        axisLabel: {
          formatter: (value: number) => `${Math.round(value / 100)} €`,
        },
      },
      yAxis: {
        type: "category",
        data: merchants.map(
          (entry) => entry.merchant ?? "Esercente non indicato",
        ),
        axisLabel: { width: 140, overflow: "truncate" },
      },
      series: [
        {
          name: "Importo netto",
          type: "bar",
          data: merchants.map((entry) => entry.amount.minor_units),
        },
      ],
    });
    const resize = () => chart.resize();
    window.addEventListener("resize", resize);
    return () => {
      window.removeEventListener("resize", resize);
      chart.dispose();
    };
  }, [merchants]);

  return (
    <div
      aria-label="Grafico della spesa netta per esercente"
      className="analytics-chart"
      ref={chartNode}
      role="img"
      tabIndex={0}
    />
  );
}

export function PeriodComparisonChart({
  comparison,
}: Pick<RangeOverview, "comparison">) {
  const chartNode = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (chartNode.current === null) {
      return;
    }
    const chart = echarts.init(chartNode.current, undefined, {
      renderer: "svg",
    });
    chart.setOption({
      animation: false,
      aria: {
        enabled: true,
        decal: { show: true },
        description:
          "Confronto della spesa netta registrata tra periodo corrente e precedente.",
      },
      color: ["#20364a"],
      grid: { left: 72, right: 24, top: 24, bottom: 40 },
      tooltip: { trigger: "axis", valueFormatter: euroFormatter },
      xAxis: {
        type: "category",
        data: ["Periodo corrente", "Periodo precedente"],
      },
      yAxis: {
        type: "value",
        axisLabel: {
          formatter: (value: number) => `${Math.round(value / 100)} €`,
        },
      },
      series: [
        {
          name: "Spesa netta",
          type: "bar",
          data: [
            comparison.current_total.minor_units,
            comparison.previous_total.minor_units,
          ],
        },
      ],
    });
    const resize = () => chart.resize();
    window.addEventListener("resize", resize);
    return () => {
      window.removeEventListener("resize", resize);
      chart.dispose();
    };
  }, [comparison]);

  return (
    <div
      aria-label="Grafico del confronto tra periodo corrente e precedente"
      className="analytics-chart"
      ref={chartNode}
      role="img"
      tabIndex={0}
    />
  );
}
