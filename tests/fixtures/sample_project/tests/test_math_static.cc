#include <gtest/gtest.h>

#include "math_static.h"

TEST(MathStatic, Square) { EXPECT_EQ(square(3), 9); }
TEST(MathStatic, Cube) { EXPECT_EQ(cube(2), 8); }
