# CAMDS — khảo sát Search và Create

Ngày kiểm tra: 2026-09-05. Giao diện English, phiên trình duyệt đã đăng nhập.
Danh mục máy đọc được: [authenticated_selectors.json](authenticated_selectors.json).
Đây là kết quả discovery để tích hợp tiếp; chưa nối thao tác ghi vào app.

## Phạm vi đã kiểm tra

| Khu vực | Đã khảo sát |
|---|---|
| Search / MDSs ; Modules Search | Component, Semicomponent, Material, Basic Substance, All MDSs |
| Search / Sent, Received | Nhãn trường, checkbox, dropdown, nút tìm kiếm |
| Search results | Bảng Basic Substance và All MDSs; toolbar, phân trang |
| Create / MDS | Form Component, Semicomponent, Material (classification 1.1.1) |
| Create / Module | Trang chọn loại và ba nút Create; chưa mở editor Module |
| Hộp thoại | Component Type, classification, Norms/Standards, tìm MDS/Module, tìm Substance, Tree Operations |

Đã tìm CAS `7440-02-0`, nhận đúng một dòng Nickel và mở View. Đã kiểm tra tìm
Substance trong dialog của Material, nhưng không Confirm để thêm chất vào cây.
Đã chạy Search All MDSs để kiểm tra cấu trúc bảng; không sửa hồ sơ kết quả.

## Các phát hiện ảnh hưởng trực tiếp đến app

1. Nhiều input không có `id`, `name` hoặc placeholder. Label có thể có `for`
   nhưng input tương ứng không có ID, nên `get_by_label()` chưa chắc hoạt động.
2. Dùng nhãn **con trực tiếp** của `.el-form-item`; nếu dùng `has_text` rộng,
   nhóm cha chứa các form con có thể cùng khớp.
3. Search có tabpanel rỗng: các bộ lọc nằm ngoài tabpanel. Không scope bộ lọc
   Search vào `get_by_role('tabpanel')`. Form Create thì nằm trong `Details`.
4. Dialog `Detail` dùng lại nhiều nhãn của trang nền. Khi dialog mở, mọi locator
   tìm kiếm phải scope vào dialog, tránh điền nhầm form Create.
5. Dropdown Element UI không phải `<select>`. Không dùng `select_option()`.
   Mở input/caret rồi chọn option đang hiển thị. Không dùng ID `dropdown-menu-*`
   hoặc thuộc tính Vue `data-v-*` vì chúng có thể thay đổi.
6. Bảng All MDSs có bản sao cột cố định, dẫn đến lặp dòng và nút View. Không dùng
   `.first()` để che lỗi selector mơ hồ. Cần scope bảng chính và khớp ID/version.
7. SPA có trạng thái chuyển tiếp hiển thị form Component trước khi Material hoặc
   Semicomponent tải xong. Chờ đúng Type, route và field của loại đích.
8. Icon `Add Metarial` bị viết sai ngay trên trang; selector phải giữ nguyên.
9. `newSearch` quay lại tab Component; app cần chọn lại tab mong muốn.

## Mẫu locator cho app Python/Playwright

```python
import json

def form_item(scope, label):
    # :text-is normalizes whitespace; direct child excludes ancestor form groups.
    return scope.locator(
        '.el-form-item:has(> .el-form-item__label:text-is('
        + json.dumps(label, ensure_ascii=False) + '))'
    )

# Search Material — khi không có dialog
material_name = form_item(page, 'Material Name:').locator('input')
search_button = page.get_by_role('button', name='Search', exact=True)

# Create Material — chỉ tạo locator, không tự điền hoặc lưu
details = page.get_by_role('tabpanel', name='Details', exact=True)
create_name = form_item(details, 'Material Name').locator('input')
remark = form_item(details, 'Remark').locator('textarea')

# Search Substance trong Create
dialog = page.get_by_role('dialog', name='Detail', exact=True)
cas_input = form_item(dialog, 'CAS No.:').locator('input')

# Kiểm tra trước khi dùng bất kỳ locator nào
assert await cas_input.count() == 1
assert await cas_input.is_visible()
```

Locator JSON là ứng viên dựa trên DOM quan sát được, trừ nơi có
`match_count_verified` hoặc `interaction_verified`. Không coi toàn bộ catalog
là một bài kiểm thử end-to-end đã đạt.

## Trường cần ánh xạ từ IMDS

| Dữ liệu | Component | Semicomponent | Material |
|---|---|---|---|
| Tên | Article Name | Article Name | Material Name |
| Tên ngoại ngữ | Article Name(Foreign) | Article Name(Foreign) | Name(Foreign) |
| Mã | Component No. | Semicomponent No. | Material No. |
| Khối lượng | Measured Mass per Item + mg/g/kg | Specific weight + kg/m, kg/m², kg/m³ | Chưa có trường khối lượng ở root |
| Phân loại | Component Type | Không thấy ở root | Material classification |
| Ký hiệu vật liệu | — | — | Material code/Symbol |
| Tiêu chuẩn | — | — | Norms/Standards, dialog The quoted standards |
| Ghi chú | Remark | Remark | Remark |

Tên có giới hạn 100 ký tự; mã 50; CICES 200; Remark 2000. Specific weight
ban đầu disabled khi đơn vị là `-`. Material classification là input readonly.
Các giới hạn này được đọc từ DOM, chưa kiểm thử validation phía máy chủ.

**2026-09-06 — không dùng các giới hạn này để chặn import qua API.** Chúng là
thuộc tính của form trình duyệt; đường JSON API không đi qua form đó. Preflight
chỉ báo lại, không chặn. Điều khiến việc này an toàn: mọi tên và mã đều được
đọc lại và so khớp đầy đủ sau khi lưu, nên nếu CAMDS thực sự cắt bớt thì
read-back sẽ phát hiện; và một substance không khớp chính xác vẫn dừng cả lần
chạy. Đường Playwright vẫn giữ nguyên giới hạn, vì ở đó maxlength là có thật.

## Ranh giới thao tác và phần còn chưa xác minh

Không bấm Delete, Send, Submit, Save, upload hoặc Confirm thêm node.
Ba form MDS được mở và rời đi không lưu. CAMDS hiển thị ID/version mới ngay khi
mở form; vì vậy không thể khẳng định mở Create hoàn toàn không có tác động phía
máy chủ. App discovery tự động không nên tự mở Create lặp lại.

Có hai hộp Message khác nhau: một hỏi **lưu rồi chuyển trang**, một hỏi **rời
trang khi chưa lưu**. Chỉ xác nhận rời các form khảo sát chưa lưu. Không được lập
trình tự bấm Confirm chung cho mọi hộp thoại.

Information of our company yêu cầu lưu trước khi chuyển nên chưa khảo sát nội
dung. Send status information được bỏ qua. Các field số lượng node con, phần
trăm/range/rest, recyclate, application, editor Module, biến thể classification
khác và read-back sau Save vẫn chưa được xác minh. Chúng cần một lần khảo sát
riêng trong phạm vi cho phép thay đổi bản nháp; không suy đoán selector.

## Xác minh đã thực hiện

- 6 input Component Search, 5 input Material Search và 3 input Material Create
  được đếm trực tiếp, mỗi locator khớp đúng 1 element.
- Luồng Basic Substance Search → một kết quả → View → Close → newSearch hoạt động.
- Hộp Component Type, Norms/Standards và Tree Operations được mở và đóng.
- Danh mục không chứa tài khoản, mật khẩu, nội dung hồ sơ doanh nghiệp hoặc ID
  của các form khảo sát. Không xuất toàn bộ DOM phiên đăng nhập ra file.
- Trình duyệt được để ở Search / Material khi kết thúc.
