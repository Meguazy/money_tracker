import { Table } from "@mantine/core";
import type { ReactNode } from "react";

type AccessibleTableScrollContainerProps = {
  ariaLabel: string;
  children: ReactNode;
  minWidth: number;
};

export function AccessibleTableScrollContainer({
  ariaLabel,
  children,
  minWidth,
}: AccessibleTableScrollContainerProps) {
  return (
    <Table.ScrollContainer
      minWidth={minWidth}
      scrollAreaProps={{
        viewportProps: {
          "aria-label": ariaLabel,
          role: "region",
          tabIndex: 0,
        },
      }}
    >
      {children}
    </Table.ScrollContainer>
  );
}
