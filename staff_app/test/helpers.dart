import 'dart:async';
import 'dart:io';

import 'package:bloc_test/bloc_test.dart';
import 'package:flutter/foundation.dart';
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_bloc/flutter_bloc.dart';
import 'package:staff_app/core/auth/auth_bloc.dart';
import 'package:staff_app/core/auth/staff_user.dart';
import 'package:staff_app/core/network/api_exception.dart';
import 'package:staff_app/core/realtime/live_updates.dart';
import 'package:staff_app/core/theme/app_theme.dart';
import 'package:staff_app/features/kitchen/kitchen_device.dart';
import 'package:staff_app/features/kitchen/kitchen_models.dart';
import 'package:staff_app/features/kitchen/kitchen_repository.dart';
import 'package:staff_app/features/kitchen/kitchen_store.dart';
import 'package:staff_app/features/manage/manage_repositories.dart';
import 'package:staff_app/features/menu/availability.dart';
import 'package:staff_app/features/menu/menu_models.dart';
import 'package:staff_app/features/menu/menu_repository.dart';
import 'package:staff_app/features/orders/order_draft.dart';
import 'package:staff_app/features/orders/order_models.dart';
import 'package:staff_app/features/orders/orders_repository.dart';
import 'package:staff_app/features/problems/problem_reports.dart';
import 'package:staff_app/features/tables/table_models.dart';
import 'package:staff_app/features/tables/tables_repository.dart';

class MockAuthBloc extends MockBloc<AuthEvent, AuthState> implements AuthBloc {}

/// Hand-written fakes: canned data in, recorded calls out.
class FakeTables implements TablesRepository {
  FakeTables({this.data = const [], this.pinInfo});

  List<TableSummary> data;
  PinInfo? pinInfo;
  final closed = <String>[];

  int loads = 0;

  @override
  Future<List<TableSummary>> tables() async {
    loads++;
    return data;
  }

  @override
  Future<PinInfo> pin() async => pinInfo!;

  @override
  Future<CloseTableResult> closeTable(String tableId) async {
    closed.add(tableId);
    return const CloseTableResult(sessionsEnded: 1, ordersCompleted: 1);
  }
}

class FakeOrders implements OrdersRepository {
  FakeOrders({this.data = const []});

  List<StaffOrder> data;

  @override
  Duration get serverClockOffset => Duration.zero;
  final transitions = <(StaffOrder, OrderStatus)>[];
  final placed = <(String, OrderDraft, String)>[];
  final cancelled = <StaffOrder>[];
  final requested = <(StaffOrder, String)>[];

  @override
  Future<List<StaffOrder>> orders({
    List<String> statuses = OrdersRepository.activeStatuses,
    String? tableId,
  }) async => [
    for (final o in data)
      if (tableId == null || o.tableId == tableId) o,
  ];

  @override
  Future<StaffOrder> place({
    required String tableId,
    required OrderDraft draft,
    required String idempotencyKey,
  }) async {
    placed.add((tableId, draft, idempotencyKey));
    return sampleOrder(status: OrderStatus.placed);
  }

  /// When set, the next transition fails with it (e.g. a version conflict).
  Object? failWith;

  @override
  Future<StaffOrder> transition(StaffOrder order, OrderStatus to) async {
    transitions.add((order, to));
    final failure = failWith;
    if (failure != null) {
      failWith = null;
      throw failure;
    }
    return sampleOrder(
      id: order.id,
      number: order.orderNumber,
      table: order.tableLabel,
      status: to,
      version: order.version + 1,
      createdAt: order.createdAt,
      updatedAt: DateTime.now(),
      items: order.items,
    );
  }

  final cancelReasons = <String>[];

  @override
  Future<StaffOrder> cancel(StaffOrder order, {required String reasonCode, String? reason}) async {
    cancelled.add(order);
    cancelReasons.add(reasonCode);
    return sampleOrder(id: order.id, status: OrderStatus.cancelled);
  }

  @override
  Future<StaffOrder> requestCancellation(StaffOrder order, String reason) async {
    requested.add((order, reason));
    return sampleOrder(id: order.id, status: order.status, cancelRequested: true);
  }

  final reviewed = <(StaffOrder, bool)>[];

  @override
  Future<StaffOrder> reviewCancellation(StaffOrder order, {required bool approve}) async {
    reviewed.add((order, approve));
    return sampleOrder(
      id: order.id,
      number: order.orderNumber,
      status: approve ? OrderStatus.cancelled : order.status,
      version: order.version + 1,
      updatedAt: DateTime.now(),
    );
  }
}

/// Live updates under test control: push events, flip the connection state.
class FakeLive implements LiveUpdates {
  FakeLive({LiveStatus status = LiveStatus.live}) : _status = ValueNotifier(status);

  final _events = StreamController<LiveEvent>.broadcast();
  final ValueNotifier<LiveStatus> _status;

  @override
  Stream<LiveEvent> get events => _events.stream;

  @override
  ValueListenable<LiveStatus> get status => _status;

  set connection(LiveStatus value) => _status.value = value;

  void push(LiveEvent event) => _events.add(event);

  int _n = 0;

  /// Pushes `order.created`/`order.updated` carrying [order] as the server would.
  void pushOrder(StaffOrder order, {String type = 'order.updated'}) => push(
    LiveEvent(
      id: 'evt-${_n++}',
      type: type,
      orderId: order.id,
      orderVersion: order.version,
      payload: {'order': orderJson(order)},
    ),
  );
}

class FakeKitchen implements KitchenRepository {
  FakeKitchen({this.orders = const [], this.delayMinutes = 15, DateTime? serverTime})
    : serverTime = serverTime ?? DateTime.now();

  List<StaffOrder> orders;
  int delayMinutes;
  DateTime serverTime;
  List<(String, int)> recentlyCancelled = const [];
  int loads = 0;
  Object? failWith;

  @override
  Future<KitchenBoard> board() async {
    loads++;
    final failure = failWith;
    if (failure != null) throw failure;
    return KitchenBoard(
      serverTime: serverTime,
      delayMinutes: delayMinutes,
      truncated: false,
      orders: orders,
      recentlyCancelled: recentlyCancelled,
    );
  }
}

const offlineError = ApiException(statusCode: 0, code: 'network', message: 'No connection.');

/// The board saved on the "device", in memory. Like the real store, another restaurant's
/// board is never returned.
class FakeBoardStore implements KitchenBoardStore {
  FakeBoardStore([this.saved]);

  SavedBoard? saved;
  int saves = 0;

  @override
  Future<SavedBoard?> load(String restaurantId) async => saved?.restaurantId == restaurantId ? saved : null;

  @override
  Future<void> save(SavedBoard board) async {
    saves++;
    saved = board;
  }

  @override
  Future<void> clear() async => saved = null;
}

SavedBoard savedBoard(List<StaffOrder> orders, {String restaurantId = 'r1', DateTime? syncedAt}) =>
    SavedBoard(
      restaurantId: restaurantId,
      savedAt: DateTime.now(),
      syncedAt: syncedAt ?? DateTime(2026, 10, 4, 19, 41),
      clockOffset: Duration.zero,
      delayMinutes: 15,
      truncated: false,
      orders: orders,
      tombstones: const {},
    );

/// Records what the kitchen screen asked of the tablet.
class FakeKitchenDevice implements KitchenDevice {
  FakeKitchenDevice({this.soundPreference = true});

  final played = <KitchenSound>[];
  bool awake = false;
  bool soundPreference;

  @override
  Future<void> play(KitchenSound sound) async => played.add(sound);

  @override
  Future<void> keepScreenOn(bool on) async => awake = on;

  @override
  Future<bool> soundOn() async => soundPreference;

  @override
  Future<void> setSoundOn(bool on) async => soundPreference = on;
}

/// The server's JSON for an order.
Map<String, dynamic> orderJson(StaffOrder o) => o.toJson();

class FakeMenu implements MenuRepository {
  FakeMenu([this.menu = sampleMenu]);

  final OrderingMenu menu;

  @override
  Future<OrderingMenu> orderingMenu() async => menu;
}

// Mirror of the backend role → permission matrix (subset the app uses).
const _waiter = {
  'tables.close',
  'pin.view',
  'orders.create',
  'orders.serve',
  'orders.cancel',
  'orders.cancel_request',
  'problem_reports.create',
};
const _kitchen = {
  'orders.kitchen_transition',
  'orders.cancel_request',
  'menu.availability',
  'problem_reports.create',
};
final _admin = {
  ..._waiter,
  ..._kitchen,
  'orders.cancel_privileged',
  'orders.cancel_review',
  'pin.manage',
  'menu.manage',
  'restaurant.settings.write',
  'table_sessions.invalidate_all',
  'staff.read',
  'staff.manage',
  'problem_reports.read',
};
final _owner = {..._admin, 'staff.manage_admins'};

StaffUser staff(StaffRole role, {String name = 'Ravi'}) => StaffUser(
  id: 'u-${role.name}',
  restaurantId: 'r1',
  restaurantName: 'Spice Garden',
  restaurantCode: 'spice-garden',
  username: name.toLowerCase(),
  displayName: name,
  role: role,
  mustChangePassword: false,
  permissions: switch (role) {
    StaffRole.waiter => _waiter,
    StaffRole.kitchen => _kitchen,
    StaffRole.admin => _admin,
    StaffRole.owner => _owner,
    StaffRole.unknown => const <String>{},
  },
);

MockAuthBloc signedIn(StaffUser user) {
  final auth = MockAuthBloc();
  whenListen(
    auth,
    const Stream<AuthState>.empty(),
    initialState: AuthState(status: AuthStatus.authenticated, user: user),
  );
  return auth;
}

Widget wrap(
  Widget child, {
  required AuthBloc auth,
  TablesRepository? tables,
  OrdersRepository? orders,
  MenuRepository? menu,
  KitchenRepository? kitchen,
  LiveUpdates? live,
  AvailabilityRepository? availability,
  PinRepository? pin,
  CustomerAccessRepository? customerAccess,
  StaffRepository? staffAccounts,
  ProblemReportsRepository? problems,
  ThemeData? theme,
}) {
  return MultiRepositoryProvider(
    providers: [
      RepositoryProvider<TablesRepository>.value(value: tables ?? FakeTables()),
      RepositoryProvider<OrdersRepository>.value(value: orders ?? FakeOrders()),
      RepositoryProvider<MenuRepository>.value(value: menu ?? FakeMenu()),
      RepositoryProvider<KitchenRepository>.value(value: kitchen ?? FakeKitchen()),
      RepositoryProvider<LiveUpdates>.value(value: live ?? FakeLive()),
      RepositoryProvider<AvailabilityRepository>.value(value: availability ?? FakeAvailability()),
      RepositoryProvider<PinRepository>.value(value: pin ?? FakePin()),
      RepositoryProvider<CustomerAccessRepository>.value(value: customerAccess ?? FakeCustomerAccess()),
      RepositoryProvider<StaffRepository>.value(value: staffAccounts ?? FakeStaffAccounts()),
      RepositoryProvider<ProblemReportsRepository>.value(value: problems ?? FakeProblems()),
    ],
    child: BlocProvider<AuthBloc>.value(
      value: auth,
      child: MaterialApp(debugShowCheckedModeBanner: false, theme: theme ?? AppTheme.light(), home: child),
    ),
  );
}

// --- sample data ---------------------------------------------------------------------------------

const sampleTables = [
  TableSummary(id: 't1', label: 'T1', isActive: true, activeSessions: 0, openOrders: 0, readyOrders: 0),
  TableSummary(id: 't2', label: 'T2', isActive: true, activeSessions: 2, openOrders: 1, readyOrders: 0),
  TableSummary(id: 't4', label: 'T4', isActive: true, activeSessions: 1, openOrders: 2, readyOrders: 1),
  TableSummary(id: 't5', label: 'Patio 1', isActive: true, activeSessions: 0, openOrders: 1, readyOrders: 0),
  TableSummary(id: 't6', label: 'T6', isActive: false, activeSessions: 0, openOrders: 0, readyOrders: 0),
];

StaffOrder sampleOrder({
  String id = 'o1',
  int number = 12,
  OrderStatus status = OrderStatus.ready,
  String table = 'T4',
  bool fromCustomer = false,
  bool cancelRequested = false,
  int version = 3,
  DateTime? createdAt,
  DateTime? updatedAt,
  List<OrderLine>? items,
}) => StaffOrder(
  id: id,
  orderNumber: number,
  status: status,
  version: version,
  createdAt: createdAt ?? DateTime(2026, 10, 4, 19, 42),
  updatedAt: updatedAt ?? createdAt ?? DateTime(2026, 10, 4, 19, 42),
  notes: number == 12 ? 'Birthday — bring candles' : null,
  items:
      items ??
      const [
        OrderLine(
          menuItemId: 'paneer',
          name: 'Paneer Tikka',
          quantity: 2,
          notes: 'less oil',
          modifiers: ['Spice level: Medium', 'Extras: Extra cheese'],
        ),
        OrderLine(menuItemId: 'naan', name: 'Butter Naan', quantity: 4, notes: null, modifiers: []),
      ],
  tableId: 't4',
  tableLabel: table,
  source: fromCustomer ? 'customer' : 'waiter',
  placedBy: fromCustomer ? null : 'Ravi',
  cancelRequested: cancelRequested,
  cancelRequestReason: cancelRequested ? 'Guest left early' : null,
);

/// A small dinner rush, timed relative to "now" on the server.
List<StaffOrder> sampleRush(DateTime serverNow) {
  DateTime ago(int minutes) => serverNow.subtract(Duration(minutes: minutes));
  const paneer = OrderLine(
    menuItemId: 'paneer',
    name: 'Paneer Tikka',
    quantity: 2,
    notes: null,
    modifiers: ['Spice level: Medium'],
  );
  const naan = OrderLine(menuItemId: 'naan', name: 'Butter Naan', quantity: 4, notes: null, modifiers: []);
  return [
    sampleOrder(
      id: 'o1',
      number: 101,
      table: 'T4',
      status: OrderStatus.preparing,
      version: 3,
      createdAt: ago(22),
      items: const [paneer, naan],
    ),
    sampleOrder(
      id: 'o2',
      number: 102,
      table: 'T7',
      status: OrderStatus.accepted,
      version: 2,
      createdAt: ago(9),
      items: const [
        OrderLine(
          menuItemId: 'paneer',
          name: 'Paneer Tikka',
          quantity: 1,
          notes: 'no onion',
          modifiers: ['Spice level: Medium'],
        ),
      ],
    ),
    sampleOrder(
      id: 'o3',
      number: 103,
      table: 'Patio 1',
      status: OrderStatus.placed,
      version: 1,
      createdAt: ago(2),
      fromCustomer: true,
      items: const [naan],
    ),
    sampleOrder(
      id: 'o4',
      number: 99,
      table: 'T2',
      status: OrderStatus.ready,
      version: 4,
      createdAt: ago(30),
      items: const [naan],
    ),
  ];
}

const _spice = MenuGroup(
  id: 'g1',
  name: 'Spice level',
  minSelect: 1,
  maxSelect: 1,
  options: [
    MenuOption(id: 'm1', name: 'Mild', isAvailable: true),
    MenuOption(id: 'm2', name: 'Medium', isAvailable: true),
    MenuOption(id: 'm3', name: 'Hot', isAvailable: false),
  ],
);
const _extras = MenuGroup(
  id: 'g2',
  name: 'Extras',
  minSelect: 0,
  maxSelect: 2,
  options: [
    MenuOption(id: 'e1', name: 'Extra cheese', isAvailable: true),
    MenuOption(id: 'e2', name: 'Mint chutney', isAvailable: true),
  ],
);

const sampleMenu = OrderingMenu(
  categories: [
    MenuCategory(
      id: 'c1',
      name: 'Starters',
      items: [
        MenuItem(
          id: 'paneer',
          name: 'Paneer Tikka',
          description: 'Char-grilled cottage cheese',
          dietary: Dietary.veg,
          isAvailable: true,
          orderable: true,
          groupIds: ['g1', 'g2'],
        ),
        MenuItem(
          id: 'chicken',
          name: 'Chicken 65',
          description: 'Crispy, spicy fried chicken',
          dietary: Dietary.nonVeg,
          isAvailable: true,
          orderable: true,
          groupIds: [],
        ),
        MenuItem(
          id: 'bhurji',
          name: 'Egg Bhurji',
          description: null,
          dietary: Dietary.egg,
          isAvailable: false,
          orderable: false,
          groupIds: [],
        ),
      ],
    ),
    MenuCategory(
      id: 'c2',
      name: 'Breads',
      items: [
        MenuItem(
          id: 'naan',
          name: 'Butter Naan',
          description: null,
          dietary: Dietary.veg,
          isAvailable: true,
          orderable: true,
          groupIds: [],
        ),
        MenuItem(
          id: 'roti',
          name: 'Tandoori Roti',
          description: null,
          dietary: Dietary.veg,
          isAvailable: true,
          orderable: true,
          groupIds: [],
        ),
      ],
    ),
  ],
  groups: {'g1': _spice, 'g2': _extras},
);

// --- real fonts for screenshot tests -----------------------------------------------------------

Future<void> loadRealFonts() async {
  final root =
      Platform.environment['FLUTTER_ROOT'] ??
      File(Platform.resolvedExecutable).parent.parent.parent.parent.parent.parent.path;
  final dir = '$root/bin/cache/artifacts/material_fonts';
  Future<ByteData> read(String name) async =>
      ByteData.view(Uint8List.fromList(await File('$dir/$name').readAsBytes()).buffer);
  final roboto = FontLoader('Roboto');
  for (final weight in ['regular', 'medium', 'bold', 'black', 'italic']) {
    roboto.addFont(read('roboto-$weight.ttf'));
  }
  await roboto.load();
  await (FontLoader('MaterialIcons')..addFont(read('materialicons-regular.otf'))).load();
}

// --- manager tools (Phase 11) ---------------------------------------------------------------------

const sampleAvailability = AvailabilityMenu(
  dishes: [
    Switchable(id: 'paneer', name: 'Paneer Tikka', section: 'Starters', isAvailable: true),
    Switchable(id: 'chicken65', name: 'Chicken 65', section: 'Starters', isAvailable: false),
    Switchable(id: 'naan', name: 'Butter Naan', section: 'Breads', isAvailable: true),
  ],
  options: [Switchable(id: 'cheese', name: 'Extra cheese', section: 'Extras', isAvailable: true)],
);

class FakeAvailability implements AvailabilityRepository {
  FakeAvailability([this.menu = sampleAvailability]);

  AvailabilityMenu menu;
  final changes = <(String, bool)>[];

  /// Hide/show calls: (dish id, shown).
  final shownChanges = <(String, bool)>[];
  int loads = 0;
  bool? lastIncludeHidden;
  Object? failWith;

  @override
  Future<AvailabilityMenu> load({bool includeHidden = false}) async {
    loads++;
    lastIncludeHidden = includeHidden;
    if (includeHidden) return menu;
    return AvailabilityMenu(
      dishes: [
        for (final d in menu.dishes)
          if (!d.isHidden) d,
      ],
      options: menu.options,
    );
  }

  Future<bool> _set(String id, bool available) async {
    final failure = failWith;
    if (failure != null) {
      failWith = null;
      throw failure;
    }
    changes.add((id, available));
    for (final s in [...menu.dishes, ...menu.options]) {
      if (s.id == id) menu = menu.replace(s.withAvailable(available));
    }
    return available;
  }

  @override
  Future<bool> setDish(String id, {required bool available}) => _set(id, available);

  @override
  Future<bool> setOption(String id, {required bool available}) => _set(id, available);

  @override
  Future<bool> setDishShown(String id, {required bool shown}) async {
    final failure = failWith;
    if (failure != null) {
      failWith = null;
      throw failure;
    }
    shownChanges.add((id, shown));
    for (final d in menu.dishes) {
      if (d.id == id) menu = menu.replace(d.withHidden(!shown));
    }
    return shown;
  }
}

class FakeCustomerAccess implements CustomerAccessRepository {
  FakeCustomerAccess([CustomerAccess? access])
    : access =
          access ??
          const CustomerAccess(
            locationEnabled: false,
            latitude: null,
            longitude: null,
            radiusM: 100,
            pinFallback: true,
          );

  CustomerAccess access;
  final saved = <CustomerAccess>[];
  Object? failWith;

  @override
  Future<CustomerAccess> current() async => access;

  @override
  Future<CustomerAccess> save(CustomerAccess next) async {
    final failure = failWith;
    if (failure != null) {
      failWith = null;
      throw failure;
    }
    saved.add(next);
    return access = next;
  }
}

class FakePin implements PinRepository {
  FakePin([PinSettings? settings])
    : settings =
          settings ??
          PinSettings(
            pin: '4827',
            version: 3,
            pinLength: 4,
            rotationMinutes: 240,
            nextRotationAt: DateTime(2026, 10, 4, 22),
          );

  PinSettings settings;
  final rotations = <bool>[];
  final saved = <(int?, int)>[];
  int endAllCalls = 0;

  @override
  Future<PinSettings> current() async => settings;

  @override
  Future<(PinSettings, int)> rotate({required bool endSessions}) async {
    rotations.add(endSessions);
    settings = PinSettings(
      pin: '1593',
      version: settings.version + 1,
      pinLength: settings.pinLength,
      rotationMinutes: settings.rotationMinutes,
      nextRotationAt: settings.nextRotationAt,
    );
    return (settings, endSessions ? 3 : 0);
  }

  @override
  Future<PinSettings> save({required int? rotationMinutes, required int pinLength}) async {
    saved.add((rotationMinutes, pinLength));
    settings = PinSettings(
      pin: settings.pin,
      version: settings.version,
      pinLength: pinLength,
      rotationMinutes: rotationMinutes,
      nextRotationAt: rotationMinutes == null ? null : settings.nextRotationAt,
    );
    return settings;
  }

  @override
  Future<int> endAllSessions() async {
    endAllCalls++;
    return 5;
  }
}

final sampleStaff = [
  StaffMember(
    id: 'u-owner',
    username: 'asha',
    displayName: 'Asha',
    role: StaffRole.owner,
    isActive: true,
    mustChangePassword: false,
    lastLoginAt: DateTime(2026, 10, 4, 18),
  ),
  const StaffMember(
    id: 'u-ravi',
    username: 'ravi',
    displayName: 'Ravi',
    role: StaffRole.waiter,
    isActive: true,
    mustChangePassword: false,
    lastLoginAt: null,
  ),
  const StaffMember(
    id: 'u-meena',
    username: 'meena',
    displayName: 'Meena',
    role: StaffRole.kitchen,
    isActive: false,
    mustChangePassword: false,
    lastLoginAt: null,
  ),
];

class FakeStaffAccounts implements StaffRepository {
  FakeStaffAccounts([List<StaffMember>? members]) : members = members ?? [...sampleStaff];

  List<StaffMember> members;
  final added = <(String, String, StaffRole, String)>[];
  final resets = <(String, String)>[];
  final updates = <(String, StaffRole?, bool?)>[];

  @override
  Future<List<StaffMember>> list() async => members;

  @override
  Future<StaffMember> add({
    required String username,
    required String displayName,
    required StaffRole role,
    required String temporaryPassword,
  }) async {
    added.add((username, displayName, role, temporaryPassword));
    final member = StaffMember(
      id: 'u-$username',
      username: username,
      displayName: displayName,
      role: role,
      isActive: true,
      mustChangePassword: true,
      lastLoginAt: null,
    );
    members = [...members, member];
    return member;
  }

  @override
  Future<StaffMember> update(String id, {StaffRole? role, bool? isActive}) async {
    updates.add((id, role, isActive));
    final m = members.firstWhere((m) => m.id == id);
    return StaffMember(
      id: m.id,
      username: m.username,
      displayName: m.displayName,
      role: role ?? m.role,
      isActive: isActive ?? m.isActive,
      mustChangePassword: m.mustChangePassword,
      lastLoginAt: m.lastLoginAt,
    );
  }

  @override
  Future<void> resetPassword(String id, String temporaryPassword) async =>
      resets.add((id, temporaryPassword));
}

class FakeProblems implements ProblemReportsRepository {
  FakeProblems([this.reports = const []]);

  List<ProblemReport> reports;
  final sent = <(ProblemCategory, String?, String?)>[];

  @override
  Future<void> report(ProblemCategory category, {String? note, String? orderId}) async =>
      sent.add((category, note, orderId));

  @override
  Future<List<ProblemReport>> recent() async => reports;
}
