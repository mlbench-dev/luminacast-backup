declare module "react-rnd" {
  import { ComponentType, CSSProperties, ReactNode, HTMLAttributes } from "react";

  interface RndProps extends HTMLAttributes<HTMLDivElement> {
    size?: { width: number | string; height: number | string };
    position?: { x: number; y: number };
    bounds?: string | Element;
    enableResizing?: boolean | Record<string, boolean>;
    onDragStop?: (e: unknown, d: { x: number; y: number }) => void;
    onResizeStop?: (e: unknown, dir: string, ref: HTMLElement, delta: { width: number; height: number }, pos: { x: number; y: number }) => void;
    children?: ReactNode;
  }

  export const Rnd: ComponentType<RndProps>;
}
