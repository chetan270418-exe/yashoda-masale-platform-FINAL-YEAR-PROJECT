import React from "react";

export default function LoginPage() {
  return (
    <main style={{ padding: "3rem 1rem", maxWidth: "420px", margin: "0 auto" }}>
      <div style={{ border: "1px solid #ccc", borderRadius: "8px", padding: "2rem", backgroundColor: "#fff" }}>
        <h2 style={{ textAlign: "center", color: "#7B1113" }}>Sign In to SpiceChain</h2>
        <form style={{ display: "flex", flexDirection: "column", gap: "1rem" }}>
          <div>
            <label style={{ display: "block", marginBottom: "0.5rem" }}>Email</label>
            <input
              type="email"
              placeholder="user@example.com"
              style={{ width: "100%", padding: "0.5rem", borderRadius: "4px", border: "1px solid #ccc" }}
            />
          </div>
          <div>
            <label style={{ display: "block", marginBottom: "0.5rem" }}>Password</label>
            <input
              type="password"
              style={{ width: "100%", padding: "0.5rem", borderRadius: "4px", border: "1px solid #ccc" }}
            />
          </div>
          <button
            type="submit"
            style={{
              padding: "0.75rem",
              backgroundColor: "#7B1113",
              color: "#fff",
              border: "none",
              borderRadius: "4px",
              cursor: "pointer",
            }}
          >
            Sign In
          </button>
        </form>
      </div>
    </main>
  );
}
