declare module "asciichart" {
  export function plot(data: number[], options?: { height?: number; padding?: number }): string;
}

declare module "drawille" {
  class Canvas {
    constructor(width?: number, height?: number);
    set(x: number, y: number): void;
    unset(x: number, y: number): void;
    toggle(x: number, y: number): void;
    clear(): void;
    frame(delimiter?: string): string;
  }
  export = Canvas;
}
