import 'package:audioplayers/audioplayers.dart';
import 'package:flutter/foundation.dart';
import 'package:shared_preferences/shared_preferences.dart';
import 'package:wakelock_plus/wakelock_plus.dart';

enum KitchenSound {
  /// A bright rising chime.
  newOrder('sounds/new_order.wav'),

  /// A lower, falling double tone: an order being cooked was cancelled.
  cancelled('sounds/order_cancelled.wav');

  const KitchenSound(this.asset);

  final String asset;
}

/// What the kitchen screen needs from the tablet besides the network: sounds, keeping the
/// screen on, and remembering whether this tablet's sound is on.
abstract interface class KitchenDevice {
  Future<void> play(KitchenSound sound);
  Future<void> keepScreenOn(bool on);
  Future<bool> soundOn();
  Future<void> setSoundOn(bool on);
}

class PluginKitchenDevice implements KitchenDevice {
  PluginKitchenDevice();

  static const _soundKey = 'kitchen_sound_on';
  AudioPlayer? _player;

  Future<AudioPlayer> _ready() async {
    final existing = _player;
    if (existing != null) return existing;
    final player = _player = AudioPlayer();
    await player.setReleaseMode(ReleaseMode.stop);
    // Like an alarm: briefly lowers music playing on the tablet instead of stopping it, and
    // plays even if the tablet is set to silent.
    await player.setAudioContext(AudioContextConfig(focus: AudioContextConfigFocus.duckOthers).build());
    return player;
  }

  @override
  Future<void> play(KitchenSound sound) async {
    try {
      final player = await _ready();
      await player.stop();
      await player.play(AssetSource(sound.asset), mode: PlayerMode.lowLatency);
    } catch (error) {
      debugPrint('Kitchen sound failed: ${error.runtimeType}'); // a silent tablet still works
    }
  }

  @override
  Future<void> keepScreenOn(bool on) async {
    try {
      await WakelockPlus.toggle(enable: on);
    } catch (error) {
      debugPrint('Keep-screen-on failed: ${error.runtimeType}');
    }
  }

  @override
  Future<bool> soundOn() async {
    try {
      return (await SharedPreferences.getInstance()).getBool(_soundKey) ?? true;
    } catch (_) {
      return true;
    }
  }

  @override
  Future<void> setSoundOn(bool on) async {
    try {
      await (await SharedPreferences.getInstance()).setBool(_soundKey, on);
    } catch (_) {
      // Not remembered across restarts; the switch still works now.
    }
  }
}
