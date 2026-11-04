"""Small authored gestures; one motion owner, no detached background jobs."""
from .scenes import Scene

def poses(scene: Scene) -> list[tuple[int, int, int, float]]:
    pan, tilt = scene.pan, scene.tilt
    def pose(p, t, duration, pause=0.0):
        return max(70, min(110, p)), max(100, min(125, t)), duration, pause
    if scene.gesture == 'peek':
        return [pose(pan - 3, tilt - 5, 800, .18),
                pose(pan + 2, tilt + 2, 950, .25), pose(pan, tilt, 700)]
    if scene.gesture == 'nod':
        # Pick the direction with available room even at the lower limit.
        delta = -4 if tilt >= 104 else 4
        return [pose(pan, tilt, scene.duration_ms, .15),
                pose(pan, tilt + delta, 650, .12), pose(pan, tilt, 850)]
    if scene.gesture == 'settle':
        delta = 3 if pan <= 107 else -3
        return [pose(pan, tilt, scene.duration_ms, .25),
                pose(pan + delta, tilt - 2, 800, .15), pose(pan, tilt, 900)]
    return [pose(pan, tilt, scene.duration_ms)]
