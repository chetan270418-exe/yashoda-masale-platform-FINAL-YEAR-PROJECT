import React from "react";

export interface ButtonProps extends React.ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: "primary" | "secondary" | "danger";
}

export function Button({ variant = "primary", style, children, ...props }: ButtonProps) {
  const bg = variant === "primary" ? "#7B1113" : variant === "danger" ? "#b91c1c" : "#e5e7eb";
  const color = variant === "secondary" ? "#111827" : "#ffffff";
  return (
    <button
      style={{
        padding: "0.5rem 1rem",
        backgroundColor: bg,
        color,
        border: "none",
        borderRadius: "4px",
        cursor: "pointer",
        fontWeight: 500,
        ...style,
      }}
      {...props}
    >
      {children}
    </button>
  );
}
