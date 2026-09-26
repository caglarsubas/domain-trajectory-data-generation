import "./globals.css";
import { Fraunces, Figtree } from "next/font/google";

const display = Fraunces({ subsets: ["latin"], variable: "--font-display" });
const sans = Figtree({ subsets: ["latin"], variable: "--font-sans" });

export const metadata = {
  title: "Trajectory studio",
  description: "Configure, inspect, and re-run domain trajectory studies.",
};

export default function RootLayout({ children }) {
  return (
    <html lang="en" className={`${display.variable} ${sans.variable}`}>
      <body>{children}</body>
    </html>
  );
}
