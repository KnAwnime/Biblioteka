#!/bin/bash
##############################################################################
# Example command to build the android target.
##############################################################################
# 
# This script shows how one can build a Caffe2 binary for the Android platform
# using android-cmake. A few notes:
#
# (1) This build also does a host build for protobuf. You will need autoconf
#     to carry out this. If autoconf is not possible, you will need to provide
#     a pre-built protoc binary that is the same version as the protobuf
#     version under third_party.
#     If you are building on Mac, you might need to install autotool and
#     libtool. The easiest way is via homebrew:
#         brew install automake
#         brew install libtool
# (2) You will need to have android ndk installed. The current script assumes
#     that you are installing it under /opt. If not, you will need to adjust
#     accordingly.
# (3) The toolchain and the build target platform can be specified with the
#     cmake arguments below. For more details, check out android-cmake's doc.

CAFFE2_ROOT="$( cd "$(dirname "$0")"/.. ; pwd -P)"
echo "Caffe2 build root is: $CAFFE2_ROOT"

# We are going to build the target into build_android.
BUILD_ROOT=$CAFFE2_ROOT/build_android
mkdir -p $BUILD_ROOT

# First, build protobuf from third_party, and copy the resulting protoc
# binary to the build root directory.
echo "Building protoc"
cd $CAFFE2_ROOT/third_party/protobuf
./autogen.sh || exit 1
./configure --prefix=$BUILD_ROOT/protoc || exit 1
make -j 4 || exit 1
make install || exit 1
make clean

# Now, actually build the android target. Let's find the most recent version
# of android ndk under /opt/android_ndk.
NDK_VERSION="$(ls -1 /opt/android_ndk/ | sort | tail -1)"
if [ -z "$NDK_VERSION" ]; then
    echo "Cannot find ndk: did you install it under /opt/android_ndk?"
    exit 1
fi
NDK_ROOT=/opt/android_ndk/$NDK_VERSION/
echo "Using Android ndk at $NDK_ROOT"

echo "Building caffe2"
cd $BUILD_ROOT

cmake .. \
    -DCMAKE_TOOLCHAIN_FILE=../third_party/android-cmake/android.toolchain.cmake \
    -DCMAKE_INSTALL_PREFIX=../install \
    -DANDROID_NDK=$NDK_ROOT \
    -DCMAKE_BUILD_TYPE=Release \
    -DANDROID_ABI="armeabi-v7a with NEON" \
    -DANDROID_NATIVE_API_LEVEL=21 \
    -DUSE_CUDA=OFF \
    -DBUILD_TEST=OFF \
    -DUSE_LMDB=OFF \
    -DUSE_LEVELDB=OFF \
    -DBUILD_PYTHON=OFF \
    -DPROTOBUF_PROTOC_EXECUTABLE=$BUILD_ROOT/protoc/bin/protoc \
    -DCMAKE_VERBOSE_MAKEFILE=1 \
    -DUSE_MPI=OFF \
    -DBUILD_SHARED_LIBS=OFF \
    -DCMAKE_CXX_FLAGS_RELEASE=-s \
    || exit 1
make
