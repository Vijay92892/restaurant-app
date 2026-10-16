// Shapes returned by the backend's /api/v1/public endpoints.
// Prices are informational menu text only — never totalled or billed.

export type Dietary = "veg" | "non_veg" | "egg";

export interface Landing {
  restaurant_name: string;
  table_label: string;
  pin_length: number;
  has_session: boolean;
  /** A built-in theme of this page ("heritage"), or null for the default look. */
  theme: string | null;
  /** A short line under the restaurant's name, once the restaurant approved it. */
  tagline: string | null;
}

export interface SessionInfo {
  restaurant_name: string;
  table_label: string;
  expires_at: string;
}

export interface MenuOption {
  id: string;
  name: string;
  display_price_delta_paise: number | null;
  is_available: boolean;
}

export interface MenuGroup {
  id: string;
  name: string;
  min_select: number;
  max_select: number;
  options: MenuOption[];
}

export interface MenuItem {
  id: string;
  name: string;
  description: string | null;
  dietary: Dietary | null;
  display_price_paise: number | null;
  image_url: string | null;
  thumbnail_url: string | null;
  is_available: boolean;
  orderable: boolean;
  modifier_group_ids: string[];
}

export interface MenuCategory {
  id: string;
  name: string;
  description: string | null;
  items: MenuItem[];
}

export interface PublicMenu {
  restaurant_name: string;
  table_label: string;
  categories: MenuCategory[];
  modifier_groups: MenuGroup[];
}

export type OrderStatus =
  | "placed"
  | "accepted"
  | "preparing"
  | "ready"
  | "served"
  | "completed"
  | "cancelled";

export interface OrderLine {
  id: string;
  name: string;
  quantity: number;
  notes: string | null;
  modifiers: string[];
}

export interface Order {
  id: string;
  order_number: number;
  status: OrderStatus;
  version: number;
  created_at: string;
  notes: string | null;
  cancel_requested: boolean;
  items: OrderLine[];
}

export interface OrderLineRequest {
  menu_item_id: string;
  quantity: number;
  modifier_ids: string[];
  notes?: string;
}

export interface OrderRequest {
  items: OrderLineRequest[];
  notes?: string;
}
