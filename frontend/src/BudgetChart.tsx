import { useEffect, useRef } from "react";
import { BarChart } from "echarts/charts";
import {
  AriaComponent,
  GridComponent,
  LegendComponent,
  TooltipComponent,
} from "echarts/components";
import * as echarts from "echarts/core";
import { SVGRenderer } from "echarts/renderers";

import type { BudgetPosition } from "./api";

echarts.use([
  BarChart,
  AriaComponent,
  GridComponent,
  LegendComponent,
  TooltipComponent,
  SVGRenderer,
]);

type BudgetChartProps = {
  budgets: BudgetPosition[];
};

export function BudgetChart({ budgets }: BudgetChartProps) {
  const chartNode = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (chartNode.current === null) {
      return;
    }
    const chart = echarts.init(chartNode.current, undefined, { renderer: "svg" });
    chart.setOption({
      animation: false,
      aria: {
        enabled: true,
        decal: { show: true },
        description:
          "Confronto tra spesa registrata e obiettivo per ciascun budget.",
      },
      color: ["#e06b4f", "#20364a"],
      grid: { left: 120, right: 16, top: 38, bottom: 24 },
      legend: { data: ["Spesa", "Obiettivo"], top: 0 },
      tooltip: {
        trigger: "axis",
        valueFormatter: (value: number) =>
          new Intl.NumberFormat("it-IT", {
            style: "currency",
            currency: "EUR",
          }).format(value / 100),
      },
      xAxis: {
        type: "value",
        axisLabel: {
          formatter: (value: number) => `${Math.round(value / 100)} €`,
        },
      },
      yAxis: {
        type: "category",
        data: budgets.map((budget) => budget.category_name),
        axisLabel: { width: 130, overflow: "truncate" },
      },
      series: [
        {
          name: "Spesa",
          type: "bar",
          data: budgets.map((budget) => budget.posted_spend.minor_units),
        },
        {
          name: "Obiettivo",
          type: "bar",
          data: budgets.map((budget) => budget.target.minor_units),
        },
      ],
    });
    const resize = () => chart.resize();
    window.addEventListener("resize", resize);
    return () => {
      window.removeEventListener("resize", resize);
      chart.dispose();
    };
  }, [budgets]);

  return (
    <div
      aria-label="Grafico dei budget del mese"
      className="budget-chart"
      ref={chartNode}
      role="img"
      tabIndex={0}
    />
  );
}
