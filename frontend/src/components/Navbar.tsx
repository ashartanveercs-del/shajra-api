"use client";

import { useState } from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import {
  TreePine,
  Users,
  MapPin,
  Search,
  ShieldCheck,
  Menu,
  X,
  Plus,
} from "lucide-react";

const navItems = [
  { href: "/", label: "Home", icon: TreePine },
  { href: "/tree", label: "Family Tree", icon: Users },
  { href: "/map", label: "Map", icon: MapPin },
  { href: "/search", label: "Search", icon: Search },
  { href: "/admin", label: "Admin", icon: ShieldCheck },
];

export default function Navbar() {
  const pathname = usePathname();
  const [mobileOpen, setMobileOpen] = useState(false);

  return (
    <nav className="sticky top-0 z-50 bg-bg-card/90 backdrop-blur-md border-b border-border">
      <div className="mx-auto max-w-6xl px-5 sm:px-8">
        <div className="flex h-[60px] items-center justify-between">
          {/* Logo */}
          <Link href="/" className="flex items-center gap-2.5 group">
            <div className="w-8 h-8 rounded-lg bg-accent flex items-center justify-center group-hover:bg-accent-dark transition-heritage">
              <TreePine className="w-4 h-4 text-white" />
            </div>
            <div className="hidden sm:block">
              <span className="font-serif text-lg font-semibold text-text-primary tracking-tight">
                Shajra
              </span>
              <span className="text-text-light text-xs ml-1.5 font-light tracking-wide uppercase">
                Heritage
              </span>
            </div>
          </Link>

          {/* Desktop Navigation */}
          <div className="hidden md:flex items-center gap-0.5">
            {navItems.map((item) => {
              const isActive = pathname === item.href;
              const Icon = item.icon;
              return (
                <Link
                  key={item.href}
                  href={item.href}
                  className={`flex items-center gap-1.5 px-3.5 py-2 rounded-lg text-[13px] font-medium transition-heritage ${
                    isActive
                      ? "bg-accent/8 text-accent"
                      : "text-text-muted hover:text-text-primary hover:bg-bg-secondary"
                  }`}
                >
                  <Icon className="w-3.5 h-3.5" />
                  {item.label}
                </Link>
              );
            })}
          </div>

          {/* Add Member CTA */}
          <div className="hidden md:block">
            <Link
              href="/submit"
              className="btn-primary text-[13px] py-2 px-4"
            >
              <Plus className="w-3.5 h-3.5" />
              Add Member
            </Link>
          </div>

          {/* Mobile Toggle */}
          <button
            onClick={() => setMobileOpen(!mobileOpen)}
            className="md:hidden p-2 rounded-lg text-text-muted hover:text-text-primary hover:bg-bg-secondary transition-heritage"
          >
            {mobileOpen ? (
              <X className="w-5 h-5" />
            ) : (
              <Menu className="w-5 h-5" />
            )}
          </button>
        </div>
      </div>

      {/* Mobile Drawer */}
      {mobileOpen && (
        <div className="md:hidden border-t border-border bg-bg-card animate-fadeInUp">
          <div className="px-4 py-3 space-y-0.5">
            {navItems.map((item) => {
              const isActive = pathname === item.href;
              const Icon = item.icon;
              return (
                <Link
                  key={item.href}
                  href={item.href}
                  onClick={() => setMobileOpen(false)}
                  className={`flex items-center gap-3 px-4 py-3 rounded-lg text-sm font-medium transition-heritage ${
                    isActive
                      ? "bg-accent/8 text-accent"
                      : "text-text-muted hover:text-text-primary hover:bg-bg-secondary"
                  }`}
                >
              <Icon className="w-4 h-4" />
                  {item.label}
                </Link>
              );
            })}
            <Link
              href="/submit"
              className="btn-primary w-full justify-center mt-3"
            >
              <Plus className="w-4 h-4" />
              Add Family Member
            </Link>
          </div>
        </div>
      )}
    </nav>
  );
}
