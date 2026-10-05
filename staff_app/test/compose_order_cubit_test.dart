import 'package:flutter_test/flutter_test.dart';
import 'package:mocktail/mocktail.dart';
import 'package:staff_app/core/network/api_exception.dart';
import 'package:staff_app/features/menu/menu_models.dart';
import 'package:staff_app/features/menu/menu_repository.dart';
import 'package:staff_app/features/orders/compose_order_cubit.dart';
import 'package:staff_app/features/orders/order_draft.dart';
import 'package:staff_app/features/orders/order_models.dart';
import 'package:staff_app/features/orders/orders_repository.dart';

class MockMenu extends Mock implements MenuRepository {}

class MockOrders extends Mock implements OrdersRepository {}

const naan = MenuItem(
  id: 'naan',
  name: 'Butter Naan',
  description: null,
  dietary: Dietary.veg,
  isAvailable: true,
  orderable: true,
  groupIds: [],
);
const menu = OrderingMenu(
  categories: [
    MenuCategory(id: 'c', name: 'Breads', items: [naan]),
  ],
  groups: {},
);

StaffOrder placedOrder() => StaffOrder(
  id: 'o1',
  orderNumber: 7,
  status: OrderStatus.placed,
  version: 1,
  createdAt: DateTime(2026),
  updatedAt: DateTime(2026),
  notes: null,
  items: const [],
  tableId: 't1',
  tableLabel: 'T4',
  source: 'waiter',
  placedBy: 'Ravi',
  cancelRequested: false,
  cancelRequestReason: null,
);

void main() {
  late MockMenu menuRepo;
  late MockOrders ordersRepo;
  late ComposeOrderCubit cubit;
  final keys = <String>[];

  setUpAll(() => registerFallbackValue(const OrderDraft()));

  setUp(() async {
    keys.clear();
    menuRepo = MockMenu();
    ordersRepo = MockOrders();
    when(() => menuRepo.orderingMenu()).thenAnswer((_) async => menu);
    cubit = ComposeOrderCubit(tableId: 't1', menu: menuRepo, orders: ordersRepo);
    await cubit.load();
  });

  void stubPlace(List<Object> outcomes) {
    var call = 0;
    when(
      () => ordersRepo.place(
        tableId: any(named: 'tableId'),
        draft: any(named: 'draft'),
        idempotencyKey: any(named: 'idempotencyKey'),
      ),
    ).thenAnswer((invocation) async {
      keys.add(invocation.namedArguments[#idempotencyKey] as String);
      final outcome = outcomes[call++];
      if (outcome is ApiException) throw outcome;
      return outcome as StaffOrder;
    });
  }

  test('a retry after a lost connection reuses the same idempotency key', () async {
    stubPlace([const ApiException(statusCode: 0, code: 'network', message: 'offline'), placedOrder()]);
    cubit.addItem(naan, quantity: 2);
    await cubit.send();
    expect(cubit.state.status, ComposeStatus.ready);
    expect(cubit.state.draft.totalQuantity, 2); // nothing lost
    await cubit.send();
    expect(cubit.state.status, ComposeStatus.sent);
    expect(keys.length, 2);
    expect(keys[0], keys[1]);
  });

  test('changing the draft after a failure uses a fresh key', () async {
    stubPlace([const ApiException(statusCode: 0, code: 'network', message: 'offline'), placedOrder()]);
    cubit.addItem(naan);
    await cubit.send();
    cubit.addItem(naan);
    await cubit.send();
    expect(keys[0], isNot(keys[1]));
  });

  test('a dish that sold out meanwhile is removed from the draft', () async {
    stubPlace([
      const ApiException(
        statusCode: 409,
        code: 'item_unavailable',
        message: 'Sorry, Butter Naan is sold out.',
        details: {'menu_item_id': 'naan'},
      ),
    ]);
    cubit.addItem(naan);
    await cubit.send();
    expect(cubit.state.draft.isEmpty, isTrue);
    expect(cubit.state.message, 'Sorry, Butter Naan is sold out.');
    verify(() => menuRepo.orderingMenu()).called(2); // menu refreshed
  });

  test('send is ignored while already sending and when empty', () async {
    await cubit.send();
    verifyNever(
      () => ordersRepo.place(
        tableId: any(named: 'tableId'),
        draft: any(named: 'draft'),
        idempotencyKey: any(named: 'idempotencyKey'),
      ),
    );
  });
}
