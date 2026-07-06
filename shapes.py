import math

class Circle:
    """Simple Circle shape with area calculation."""

    def __init__(self, radius: float):
        """Initialize a Circle with the given radius.

        Args:
            radius (float): The radius of the circle. Must be non-negative.
        """
        if radius < 0:
            raise ValueError("Radius cannot be negative")
        self.radius = radius

    def area(self) -> float:
        """Calculate the area of the circle.

        Returns:
            float: The area computed as π * r^2.
        """
        return math.pi * (self.radius ** 2)

class Square:
    """Simple Square shape with area calculation."""

    def __init__(self, side: float):
        """Initialize a Square with the given side length.

        Args:
            side (float): Length of a side of the square. Must be non-negative.
        """
        if side < 0:
            raise ValueError("Side length cannot be negative")
        self.side = side

    def area(self) -> float:
        """Calculate the area of the square.

        Returns:
            float: The area computed as side^2.
        """
        return self.side ** 2
